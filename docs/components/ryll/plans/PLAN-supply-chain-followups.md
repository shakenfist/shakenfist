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

The remaining five items have been added to the
`[advisories].ignore` list in `deny.toml` with inline
rationale so that `cargo deny check` passes against
`develop`. This plan tracks the debt so we can pay it down.

Each ignore entry in `deny.toml` has a matching section
below. When an ignore is resolved, delete both the
`deny.toml` entry and the section here in the same PR.

## Tracked debt

### 1. `openh264-sys2` 0.6.6 heap overflow (RUSTSEC-2025-0008)

- **Ignore rationale:** the upstream fix is in version
  0.8.0, which is a semver-incompatible upgrade from 0.6.6.
  Whatever pulls `openh264` into our tree (most likely a
  transitive via `webrtc`, `gstreamer`, or a test dep)
  needs to be audited and either updated to a version that
  takes `openh264 >= 0.8`, or swapped for a different
  H.264 decoder.
- **Attack surface:** heap overflow in H.264 decoding. If
  the SPICE server streams H.264 to us (not current, but
  possible in future), a malicious server could trigger
  the overflow. Low current exposure, rising.
- **Action plan:**
  1. Identify the inclusion path
     (`cargo tree -i openh264-sys2`).
  2. Check whether the direct dependent has a newer release
     tracking openh264 0.8+.
  3. If yes, update the direct dep. If no, evaluate whether
     we need that crate at all — ryll does not currently
     use H.264 decoding so this may be removable.
- **Remove ignore when:** `openh264-sys2 >= 0.8.0` or the
  transitive inclusion is removed.

### 2. `paste` 1.0.15 unmaintained (RUSTSEC-2024-0436)

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

### 3. `rand` 0.8.5 unsound with custom logger (RUSTSEC-2026-0097)

- **Ignore rationale:** the unsoundness requires a custom
  logger that calls `rand::rng()` (or
  `rand::thread_rng()`) in the log emission path. ryll's
  logging uses `tracing` without any code path that calls
  into `rand`, so the trigger condition is not met. The
  fix requires `rand >= 0.8.6` (or 0.9.3 / 0.10.1), which
  is blocked by the transitive pull via `rsa 0.9.10`.
- **Attack surface:** none in our configuration.
- **Action plan:**
  1. Track the `rsa` crate migration path (see item 4).
     An `rsa` update will likely bring `rand` forward.
  2. If we add a custom logger in the future, re-audit
     before this ignore is in place.
- **Remove ignore when:** `rand` in our tree is
  >=0.8.6 / 0.9.3 / 0.10.1.

### 4. `rsa` 0.9.10 Marvin timing attack (RUSTSEC-2023-0071)

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

### 5. `rustls-pemfile` 2.2.0 unmaintained (RUSTSEC-2025-0134)

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

## Success criteria

This plan is complete when:

- All five `[advisories].ignore` entries in `deny.toml`
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
