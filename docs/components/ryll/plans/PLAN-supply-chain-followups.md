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

The remaining items have been added to the
`[advisories].ignore` list in `deny.toml` with inline
rationale so that `cargo deny check` passes against
`develop`. This plan tracks the debt so we can pay it down.

Each ignore entry in `deny.toml` has a matching section
below. When an ignore is resolved, delete both the
`deny.toml` entry and the section here in the same PR.

## Tracked debt

### 1. `paste` 1.0.15 unmaintained (RUSTSEC-2024-0436)

- **Ignore rationale:** the author archived the crate but
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
- **Remove ignore when:** `paste` no longer appears in
  `cargo tree`, or the RustSec advisory is withdrawn.

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

### 4. `ttf-parser` 0.25.1 unmaintained (RUSTSEC-2026-0192)

- **Ignore rationale:** the author has declared the crate
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
- **Remove ignore when:** `ttf-parser` no longer appears in
  `cargo tree`, or maintenance resumes upstream.

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
(`docs/plans/PLAN-webrtc-0.20-upgrade-phase-02-bump.md`)
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

- All `[advisories].ignore` entries in `deny.toml`
  have been removed.
- Each corresponding section above has been deleted from
  this plan file.
- `cargo deny check` still passes with no ignores in place.
- Separately tracked: duplicate-version policy has been
  tightened to `deny` (not a gate for this plan, but
  the natural next step).

## Future work

- Evaluate adding `cargo vet` once the current debt is
  cleared and we understand how noisy our baseline is.
- Consider a recurring monthly cron that opens an issue if
  new advisories appear (`cargo audit` in CI produces the
  signal; we need a mechanism to route it).
