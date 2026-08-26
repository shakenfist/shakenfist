# Audit: Rust unwrap lint

## What we check

Rust projects must enable clippy's `unwrap_used` lint so that
`.unwrap()` calls in production code are flagged, while test code is
exempted. `unwrap()` converts a recoverable error into a panic, and a
panic on data from outside the process (network input, configuration,
files, other systems) is an outage waiting to happen -- this is the
failure mode behind the November 2025 Cloudflare outage, where an
`unwrap()` on a feature file that another system had generated too
large panicked their core proxy fleet-wide.

Concretely, we require:

1. The root `Cargo.toml` sets `unwrap_used` to `warn` or `deny` in
   `[workspace.lints.clippy]` (or `[lints.clippy]` for single-crate
   repositories):

   ```toml
   [workspace.lints.clippy]
   unwrap_used = "warn"
   ```

2. A `clippy.toml` at the repository root exempts test code:

   ```toml
   allow-unwrap-in-tests = true
   ```

3. Every first-party crate manifest either inherits the workspace
   lints or defines the lint itself:

   ```toml
   [lints]
   workspace = true
   ```

   Fuzz harness crates (any `Cargo.toml` under a `fuzz` directory)
   are exempt -- their whole purpose is to crash noisily on bad
   input.

`warn` in `Cargo.toml` combined with `-D warnings` in the CI clippy
run is the preferred arrangement: local iterative builds are not
blocked mid-refactor, but nothing lands with a new production
`unwrap()`. Setting `deny` directly is also accepted.

Note what this audit deliberately does **not** require:

* We do not lint `expect_used`. Replacing a provably-infallible
  `unwrap()` with `expect("why this cannot fail")` is the sanctioned
  fix -- it documents the invariant and produces a self-explanatory
  panic message.
* Idiomatic poisoning panics such as `mutex.lock().unwrap()` may be
  kept via a scoped `#[allow(clippy::unwrap_used)]` (ideally with a
  comment), converted to `.expect("lock poisoned")`, or wrapped in a
  small helper. Escalating a panic rather than running on
  possibly-corrupt shared state is usually correct.
* Zero panics. Slice indexing, `assert!` and arithmetic overflow can
  still panic; this audit targets the most common way untrusted input
  becomes a panic, not the entire panic surface.

## Template

No template -- this is a code-level pattern. See the configuration
snippets above.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-26T06:56:26.297909+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | N/A | - |
| agent-python | N/A | - |
| client-python | N/A | - |
| client-python-k3s | N/A | - |
| clingwrap | N/A | - |
| cloudgood | N/A | - |
| development | N/A | - |
| divergulent | N/A | - |
| instar | compliant | - |
| kerbside | N/A | - |
| kerbside-patches | N/A | - |
| library-utilities | N/A | - |
| occystrap | N/A | - |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | N/A | - |
| shakenfist | N/A | - |
<!-- consistency-audit:end -->
