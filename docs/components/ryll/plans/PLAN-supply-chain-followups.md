# Supply-chain follow-ups

## Situation

While landing deterministic supply-chain scanners (see
`PLAN-supply-chain-scanning.md`) the initial `cargo deny
check` run against `develop` surfaced a set of advisories
and unmaintained crates. Two were fixable in-place and
were folded into the scanners PR:

- `RUSTSEC-2026-0098` + `RUSTSEC-2026-0099` — rustls-webpki
  0.103.10 name-constraint bypass vulnerabilities.
  Resolved by `cargo update -p rustls-webpki` to 0.103.12.

Since then, further resolved:

- `RUSTSEC-2025-0008` — openh264-sys2 heap overflow. Resolved
  when the tree moved to openh264-sys2 0.9.6 (fix was in
  >=0.8.0); the stale ignore was removed in July 2026.
- `RUSTSEC-2026-0097` — rand unsound with a custom logger.
  Resolved when the tree moved to rand 0.8.8 (fix was in
  >=0.8.6).
- `RUSTSEC-2026-0150` — audiopus_sys unmaintained. Resolved
  when `opus` 0.4.0 switched its sys crate from
  `audiopus_sys` to `opusic-sys`, dropping the crate from
  the tree entirely.
- `RUSTSEC-2026-0194` + `RUSTSEC-2026-0195` — quick-xml DoS
  pair. Resolved when `wayland-scanner` and `zbus_xml` both
  moved to quick-xml 0.41.0.

All four were removed from `deny.toml` and
`.cargo/audit.toml` in September 2026, after `cargo deny`
reported them as `advisory-not-detected`.

In November 2026 eight more ignore entries were removed
although nothing they covered had been fixed: six items, 1 and
4-8 below, with item 5 covering three advisory IDs.
Run with no ignores at all, neither scanner failed on any of
them, and the `cargo audit` and `cargo deny` lanes both
passed once they were gone (CI run 45772182263, where
`cargo audit` lists all eight as warnings). All are
unmaintained-status advisories against transitive
dependencies. As observed in November 2026, cargo-deny checks
unmaintained and unsound advisories only for the workspace's
direct dependencies by default; `deny.toml` now states that
scope explicitly (`unmaintained` and `unsound`), so it no
longer rests on a default. Both are real scope keys: the
`cargo deny` lane on the tree that set them (CI run
45789484154) reported no unknown-field diagnostic, and
widening both to `"all"` locally makes cargo-deny fail on the
tracked warnings below and on transitive unsound advisories
in `heapless` and `lru`. cargo-deny never reported the
eight and flagged each ignore as `advisory-not-detected`;
cargo audit reports them as warnings, which do not fail the
job. The ignores gated nothing, and their presence implied a
gate that did not exist. The debt itself is still real, so
those items stay below under *Tracked warnings*.

The remaining items are on the `[advisories].ignore` lists
with inline rationale so that the scanners pass against
`develop`: both in `deny.toml`, and RUSTSEC-2023-0071 alone in
`.cargo/audit.toml`, because cargo audit does not fail on
RUSTSEC-2025-0134. Each list holds only what its own scanner
fails on. Because cargo audit warns about RUSTSEC-2025-0134
without ignoring it, the weekly cron tracks it with an issue
too, as it does the tracked warnings below; an ignore entry
and an issue for the same advisory is expected, not a
double count. This plan
tracks the debt so we can pay it down.

Each ignore entry has a matching section below. When an
ignore is resolved, delete the entry and the section here in
the same PR. Before adding an ignore, confirm the scanner
actually fails without it.

Item numbers are stable identifiers: `deny.toml`,
`.cargo/audit.toml` and other sections of this file cite them,
so an item keeps its number when it moves between sections,
and the sections are not in numeric order.

## Tracked debt: ignored advisories

### 2. `rsa` 0.9.10 Marvin timing attack (RUSTSEC-2023-0071)

- **Ignore rationale:** no fixed version is available in
  the ecosystem. The RustSec advisory has been open since
  2023. The attack requires timing observation of RSA
  operations performed by the ryll client; the attacker
  would need to be on the same machine or have a
  high-precision network timing side-channel.
- **Attack surface:** whatever uses `rsa` in our tree —
  likely TLS key operations or SPICE ticket decryption.
  Needs confirmation via `cargo tree -i rsa`.
- **Action plan:**
  1. Identify direct dependents via `cargo tree -i rsa`.
  2. Check whether our usage performs RSA operations on
     attacker-observable timing (SPICE ticket decrypt is
     a plausible candidate).
  3. If yes, consider constant-time RSA alternatives
     (`rustls`'s crypto provider, `boring-rs`, etc.) or
     switch the SPICE auth path to a non-RSA scheme.
  4. Track the `rsa` crate for a fix — subscribe to the
     RustSec advisory or the `rsa` crate releases.
- **Remove ignore when:** a patched `rsa` version ships,
  or we migrate off the `rsa` crate.

### 3. `rustls-pemfile` 2.2.0 unmaintained (RUSTSEC-2025-0134)

- **Ignore rationale:** the repository was archived in
  August 2025. Functionality has been incorporated into
  `rustls-pki-types >= 1.9.0` via the `PemObject` trait.
  The latest `rustls-pemfile` is effectively a re-export
  shim. Migration is a code change in whatever uses it.
- **Attack surface:** PEM parsing surface for our TLS
  setup. No known exploit — the advisory is
  unmaintained-status, not a vulnerability.
- **Action plan:**
  1. `cargo tree -i rustls-pemfile` to find our usage.
  2. Migrate our TLS setup to `rustls-pki-types`
     `PemObject` APIs. This may be a direct code change in
     ryll or may need to wait for an upstream dep to
     migrate.
  3. Drop `rustls-pemfile` from `Cargo.toml`.
- **Remove ignore when:** `rustls-pemfile` no longer
  appears in `cargo tree`.

## Tracked warnings: unmaintained transitive dependencies

These advisories fail neither scanner, so they carry no
ignore: cargo audit lists them as warnings on every run, and
cargo-deny does not check them at all. They are tracked here
because each is still debt worth paying down. Each is also
tracked by a GitHub issue that the weekly `Supply chain`
cron's `rustsec/audit-check` files for any warning no issue or
pull request title names yet; this section keeps the
reasoning, the issue the status. Leave the issue open until
the crate leaves `cargo tree`. If one turns
into a vulnerability advisory, or the crate becomes a direct
dependency, the scanners will fail and it moves back up to
the ignored list (or, better, gets fixed).

### 1. `paste` 1.0.15 unmaintained (RUSTSEC-2024-0436)

- **Rationale:** the author archived the crate but
  did not publish a fix or a CVE. A drop-in fork (`pastey`)
  exists with the same API. `paste` reaches us transitively.
- **Attack surface:** none direct; it is a proc-macro for
  identifier pasting used at compile time. The risk is
  only that it no longer receives bug fixes.
- **Action plan:**
  1. `cargo tree -i paste` to find direct dependents.
  2. For each direct dependent we control, swap to `pastey`
     if they accept the patch, or vendor-patch.
  3. For upstream dependents we don't control, wait for
     their migration. Re-check every 6 months.
- **Resolved when:** `paste` no longer appears in
  `cargo tree`, or the RustSec advisory is withdrawn.

### 4. `ttf-parser` 0.25.1 unmaintained (RUSTSEC-2026-0192)

- **Rationale:** the author has declared the crate
  unmaintained and recommends `skrifa`. It reaches us via
  egui's font stack (`ab_glyph` -> `owned_ttf_parser` ->
  `ttf-parser`), so there is nothing to change on our side
  until egui (or ab_glyph) migrates.
- **Attack surface:** font parsing of the fonts we embed
  (epaint default fonts) plus any user-configured fonts.
  Unmaintained-status advisory, not a vulnerability; the
  concern is future parsing bugs going unfixed.
- **Action plan:**
  1. Track egui / ab_glyph for a migration to `skrifa` or
     another maintained parser.
  2. Re-check on each eframe upgrade (we already take these
     via renovate).
- **Resolved when:** `ttf-parser` no longer appears in
  `cargo tree`, or maintenance resumes upstream.

### 5. `zvariant` / `zbus_names` / `zvariant_derive` unmaintained (RUSTSEC-2026-0370, -0371, -0372)

- **Rationale:** upstream folded all three crates
  into `zbus` 6.0.0 (as its `zvariant` and `names` modules)
  and stopped maintaining them standalone. They reach us via
  eframe's Linux accessibility stack (`egui-winit` ->
  `accesskit_winit` -> `accesskit_unix` -> `atspi` /
  `zbus` 5.x), so there is nothing to change on our side
  until accesskit moves to `zbus` 6.
- **Attack surface:** D-Bus message (de)serialisation for
  the AT-SPI accessibility bus on Linux. Unmaintained-status
  advisories, not vulnerabilities.
- **Action plan:**
  1. Track accesskit / atspi for a migration to `zbus` 6.
  2. Re-check on each eframe upgrade (we already take these
     via renovate).
- **Resolved when:** `zbus` 5.x (and with it these
  three crates) no longer appears in `cargo tree`.

### 6. `pin-utils` 0.1.1 unmaintained (RUSTSEC-2026-0441)

- **Rationale:** the upstream repository was archived
  on 1 October 2026 after the 0.1.1 release and there is no
  successor crate, so no version bump can clear this one
  (`cargo deny` says so itself: "No safe upgrade is
  available!"). It reaches us by two transitive paths:
  `shakenfist-spice-renderer`'s `rtp` 0.17 dev-dependency ->
  `webrtc-util` 0.17.2 -> `nix` 0.26.4, and
  `ironrdp-connector` -> `sspi` -> `async-dnssd`. Both
  scanners read the whole lockfile rather than a feature
  resolution, so feature-gating a path would not silence the
  warning even where we could.
- **Attack surface:** none of substance. `pin-utils` is three
  pinning macros with no parsing, I/O or arithmetic; this is
  an unmaintained-status advisory, not a vulnerability. Its
  only widely used macro, `pin_mut!`, has a std replacement
  in `core::pin::pin!` (stable since Rust 1.68), so the
  crates that still carry it have a cheap way out when they
  get to it. Neither path reaches a shipped artefact either:
  the first is a dev-dependency used only by
  `tests/webrtc_h264_smoke.rs`, and the second arrives
  through `shakenfist-spice-rdp` behind the default-off `rdp`
  feature, which release builds do not enable. That makes
  this a weaker risk than items 2 and 3, which are in the
  shipped TLS path.
- **Action plan:**
  1. Move the renderer's `webrtc_h264_smoke` dev-dependency
     from `rtp` 0.17 onto `rtc::rtp`, which the webrtc 0.20
     port already pulls into the lockfile. That retires the
     first path along with a dead upstream line, and is
     already recorded in [PLAN-webrtc-0.20-upgrade.md](https://github.com/shakenfist/ryll/blob/7daf8a39f8759405b61be2cba10a1a2640fb7feb/docs/plans/PLAN-webrtc-0.20-upgrade.md)'s
     Future work.
  2. Track ironrdp / sspi for a release that drops
     `async-dnssd`. Re-check on each ironrdp bump; renovate
     opens those for us.
- **Resolved when:** `pin-utils` no longer appears in
  `cargo tree`.

### 7. `chrono` 0.4.45 unmaintained (RUSTSEC-2026-0543)

- **Rationale:** the advisory (published 18 November
  2026, announced in chronotope/chrono#1768) marks every
  `chrono` version unmaintained, and `cargo deny` reports "No
  safe upgrade is available!". ryll does not use `chrono`
  directly; it arrives through `dav-server` 0.11, the RFC
  4918 server behind WebDAV folder sharing, which both `ryll`
  and `shakenfist-spice-renderer` depend on. The latest
  `dav-server` (0.12.0, 9 November 2026) still requires
  `chrono ^0.4.44`, so bumping `dav-server` does not help.
- **Attack surface:** this is in the shipped path: the
  renderer's `webdav` feature is default-on. `dav-server`
  uses `chrono` to format and parse WebDAV property and HTTP
  dates for the guest's requests against a directory the
  user chose to share. The advisory is about maintenance
  status, not a known vulnerability. The exposure is that
  a future bug in that date handling would get no upstream
  fix.
- **Action plan:**
  1. Watch `dav-server` for a release that moves to `jiff`
     or `time` (the advisory's recommended alternatives).
     Renovate opens `dav-server` bumps for us; re-check
     `cargo tree -i chrono` on each.
  2. If upstream does not move, consider offering that
     migration as a patch to `dav-server`.
- **Resolved when:** `chrono` no longer appears in
  `cargo tree`.

### 8. `humantime` 2.4.0 unmaintained (RUSTSEC-2026-0542)

- **Rationale:** published with item 7. The advisory
  says `humantime` is unmaintained because `chrono` is, and
  covers every version; there is no safe upgrade. It reaches
  us only through `console-subscriber` 0.5.0, the latest
  release, which needs `humantime ^2.1.0` unconditionally.
- **Attack surface:** effectively none. `console-subscriber`
  is enabled only by ryll's diagnostic `tokio-console`
  feature (`make build-tokio-console`), which release builds
  leave out. The scanners still warn because they read the
  whole lockfile rather than a feature resolution.
- **Action plan:** the `tokio-console` feature is due to be
  removed when the K1 hang investigation closes (see the
  comment on the feature in `ryll/Cargo.toml`); that removal
  retires this item. Failing that, a `console-subscriber`
  release that drops `humantime`.
- **Resolved when:** `humantime` no longer appears in
  `cargo tree`.

## Also tracked: duplicate-version warnings

`cargo deny check` currently reports ~45 duplicate-version
warnings (set to `warn`, not `deny`). Most are in the
Windows / Wayland / macOS backend ecosystems and come from
`eframe` pulling in multiple versions of the same
platform-integration crates. These are noise for now;
tightening `multiple-versions` from `warn` to `deny` is a
later goal once we understand the baseline.

No individual action item for this — review the list
quarterly and pick off easy wins (e.g. crates where
upgrading one direct dep resolves the duplicate).

## Accepted risk: the `rtc` 0.20.x transport stack

Recorded here so the acceptance lives with the scanning
policy rather than only in the plan that caused it.

The webrtc-rs 0.20 port
([phase 02 plan](https://github.com/shakenfist/ryll/blob/5e261a025970c37192624777c9e274d65bcc887c/docs/plans/PLAN-webrtc-0.20-upgrade-phase-02-bump.md))
replaced `dtls`, `webrtc-srtp`, `webrtc-sctp`, `stun`, `turn`
and `webrtc-ice` 0.17.2 with the `rtc-*` 0.20.2 family plus
`sansio` 1.0.1 — a sans-io reimplementation of the whole
DTLS/SRTP/SCTP/STUN transport, at a version released days
before it was adopted. That code parses untrusted network
input from any peer that can reach the bound UDP sockets,
which by design is every non-loopback address on the host,
and none of this repo's own parser hardening (`BoundedReader`,
the fuzz targets, the deterministic scanners) reaches a
vendored third-party stack.

Accepted rather than mitigated, and the reasoning is that the
alternative is worse for the same threat: 0.17.x is an
abandoned line that will never receive a security fix.
Staying put means carrying known-unfixable transport code
indefinitely.

What this costs us in scanner terms, all currently reported
rather than fatal because `multiple-versions = "warn"`:
`rtc-shared` pulls in `winapi` 0.3 and `bitflags` 1.3.2, and
`quinn-udp` arrives at a duplicate major. No new
`[advisories].ignore` entries were needed in `deny.toml` and
`.cargo/audit.toml` needed no change — confirmed by the
`cargo audit` and `cargo deny` lanes both passing on the
ported tree (CI run 31910156843) rather than by inspection.

Action: treat `rtc-*` as a watch item on the weekly
`cargo audit` run. The first advisory against this family
should be triaged as though it were our own code, because in
practice it is the most exposed parser surface we ship.

## Success criteria

This plan is complete when:

- All `[advisories].ignore` entries in `deny.toml` and
  `.cargo/audit.toml` have been removed.
- Each corresponding section above has been deleted from
  this plan file.
- Every item under *Tracked warnings* has been resolved per
  its own *Resolved when*, and that section is empty.
- `cargo deny check` and `cargo audit` still pass with no
  ignores in place.
- Separately tracked: duplicate-version policy has been
  tightened to `deny` (not a gate for this plan, but
  the natural next step).

## Future work

- Evaluate adding `cargo vet` once the current debt is
  cleared and we understand how noisy our baseline is.
- Fail CI on an ignore that suppresses nothing. A stale
  entry survives until someone notices by hand -- which is
  how the September 2026 sweep found its four and the
  November 2026 one its eight. cargo-deny already names them
  as `advisory-not-detected`; for `.cargo/audit.toml`, a run
  with the list emptied shows which IDs still fail. The
  lists deliberately differ (see *Situation*), so a set
  difference between them is not the right check. A
  fleet-wide version is being planned in
  shakenfist/development.
