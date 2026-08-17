# Phase 6: Introduce ConnectionConfig and move SpiceClient

## Prompt

Before executing any step, back brief the operator. This is
the final phase of the crate extraction plan. Unlike Phases
3-5 (which were mechanical file moves), Phase 6 changes an
API boundary: `SpiceClient::new` switches from taking ryll's
`Config` to taking a new `ConnectionConfig` struct defined in
the protocol crate. The invariant is that all 99 tests pass
and ryll's behaviour is unchanged.

I prefer one commit per logical change.

## Situation

After Phases 1-5, `ryll/src/protocol/` contains only:

```
ryll/src/protocol/
├── mod.rs       # 3 lines: pub mod client; pub use client::SpiceClient;
└── client.rs    # 250 lines: SpiceClient + SpiceCaVerifier + TLS setup
```

`SpiceClient` stayed behind because it imports
[`crate::config::Config`](https://github.com/shakenfist/ryll/blob/develop/ryll/src/config.rs) at
[client.rs:17](https://github.com/shakenfist/ryll/blob/develop/ryll/src/protocol/client.rs#L17). Config
is ryll's application-level struct that bundles CLI parsing,
.vv file parsing, and SPICE connection parameters.

The research pass found:

* **SpiceClient uses 5 of Config's 6 fields**: `host`, `port`,
  `tls_port`, `ca_cert`, `password`. The 6th (`host_subject`)
  is `#[allow(dead_code)]` and unused.
* **Config is already minimal** — all 6 fields are SPICE
  connection parameters. No ryll-specific app concerns (CLI
  args, headless mode, USB disks, etc.) are mixed in. The
  broader `Args` struct holds those. So `ConnectionConfig`
  will be structurally identical to `Config`, just defined in
  the protocol crate instead of ryll.
* **Single instantiation site**:
  [app.rs:1924](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1924) —
  `SpiceClient::new(config)?;` inside `run_connection()`.
* **Single import site**:
  [app.rs:24](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L24) —
  `use crate::protocol::SpiceClient;`.
* **Three new deps** needed in the protocol crate's
  `Cargo.toml` when `client.rs` moves:
  - `socket2 = { version = "0.6", features = ["all"] }` —
    TCP keepalive (matching spice-gtk behaviour).
  - `webpki-roots = "0.26"` — system CA root certificates
    for TLS.
  - `rustls-pemfile = "2"` — PEM parsing for inline CA
    certs from .vv files.
* After Phase 6, `ryll/src/protocol/` is **deleted entirely**.

## Mission and problem statement

1. Define a `ConnectionConfig` struct in
   `shakenfist-spice-protocol` containing the 6 fields
   `SpiceClient` needs (per Decision #4 in the master plan).
2. Change `SpiceClient::new` to take `ConnectionConfig`
   instead of `Config`.
3. Move `client.rs` from ryll into the protocol crate.
4. Add a `From<&Config> for ConnectionConfig` adapter in
   ryll's `config.rs` so the single call site can bridge
   between ryll's `Config` (which owns CLI parsing) and the
   protocol crate's `ConnectionConfig` (which owns connection
   parameters).
5. Delete `ryll/src/protocol/` entirely.

After this phase:

- `SpiceClient` and `ConnectionConfig` live in
  `shakenfist-spice-protocol`.
- ryll has no `protocol/` module at all.
- ryll imports `SpiceClient` and `ConnectionConfig` from
  `shakenfist_spice_protocol`.
- ryll's `Config` (in `config.rs`) has an `impl From<&Config>
  for ConnectionConfig` adapter.
- All 99 tests pass. Behaviour is unchanged.
- The crate is still at version `0.0.0`.

## Approach

Two commits:

1. **API refactor commit** (no cross-crate file move). While
   `client.rs` stays in ryll:
   - Add `ConnectionConfig` struct to the protocol crate's
     `lib.rs`.
   - Change `client.rs` to `use shakenfist_spice_protocol::ConnectionConfig;`
     instead of `use crate::config::Config;`.
   - Add `impl From<&Config> for ConnectionConfig` in ryll's
     `config.rs`.
   - Update the call site in `app.rs` to build
     `ConnectionConfig` from `Config` before passing to
     `SpiceClient::new`.
   - Verify all tests pass. This commit proves the API
     boundary change works before any files move.

2. **Move commit** (pure mechanical, same pattern as
   Phases 3-5). Move `client.rs` into the protocol crate:
   - `git mv ryll/src/protocol/client.rs`
     `shakenfist-spice-protocol/src/client.rs`.
   - Delete `ryll/src/protocol/mod.rs` and the now-empty
     directory.
   - Remove `mod protocol;` from `ryll/src/main.rs`.
   - Add `pub mod client;` and `pub use client::SpiceClient;`
     to the protocol crate's `lib.rs`.
   - Add `socket2`, `webpki-roots`, `rustls-pemfile` to the
     protocol crate's `Cargo.toml`.
   - Update `app.rs` to import `SpiceClient` from
     `shakenfist_spice_protocol` instead of
     `crate::protocol`.

3. **Status update commit**: mark Phase 6 complete.

## Pre-flight verification

1. **Working tree is clean.**
2. **All 99 tests pass** (80 ryll + 2 compression + 17
   usbredir + 0 protocol).
3. **`cargo clippy --workspace --all-targets -- -D warnings`
   passes.**
4. **`ryll/src/protocol/` contains exactly `client.rs` and
   `mod.rs`** (2 files).
5. **Confirm `Config` struct fields** in `ryll/src/config.rs`
   match the research: `host`, `port`, `tls_port`, `password`,
   `ca_cert`, `host_subject`.
6. **Confirm `SpiceClient::new` takes `config: Config`** at
   `client.rs:114`.
7. **Confirm single `SpiceClient::new(config)` call site** at
   `app.rs:1924`.

## ConnectionConfig design

```rust
/// SPICE server connection parameters. This is the narrow
/// type that `SpiceClient` needs to establish a connection;
/// it deliberately excludes application-level concerns like
/// CLI parsing, .vv file handling, and session settings.
#[derive(Debug, Clone, Default)]
pub struct ConnectionConfig {
    pub host: String,
    pub port: u16,
    pub tls_port: Option<u16>,
    pub password: Option<String>,
    /// PEM-encoded CA certificate for TLS. When present,
    /// hostname verification is relaxed (SPICE servers
    /// commonly use self-signed certs without SAN extensions).
    pub ca_cert: Option<String>,
    /// Expected certificate subject. Currently informational
    /// only — SPICE servers commonly omit SAN extensions, so
    /// subject matching is not enforced.
    pub host_subject: Option<String>,
}
```

**Not `#[non_exhaustive]`.** Unlike `DecompressedImage` (which
represents decoder output and may gain metadata fields),
`ConnectionConfig` is a user-facing configuration struct where
struct-literal construction (`ConnectionConfig { host, port,
..Default::default() }`) is the natural ergonomic pattern. Any
new fields will be `Option<T>` with a default of `None`, which
works fine with existing struct literals via
`..Default::default()`. If `#[non_exhaustive]` is wanted later,
it can be added in the `0.1.0` release — but the team should
be aware that adding it is a breaking change (it breaks all
external struct literal construction).

## ryll's adapter

In `ryll/src/config.rs`:

```rust
use shakenfist_spice_protocol::ConnectionConfig;

impl From<&Config> for ConnectionConfig {
    fn from(c: &Config) -> Self {
        ConnectionConfig {
            host: c.host.clone(),
            port: c.port,
            tls_port: c.tls_port,
            password: c.password.clone(),
            ca_cert: c.ca_cert.clone(),
            host_subject: c.host_subject.clone(),
        }
    }
}
```

In `ryll/src/app.rs`, the call site changes from:

```rust
let client = SpiceClient::new(config)?;
```

to:

```rust
let client = SpiceClient::new(ConnectionConfig::from(&config))?;
```

(Or equivalently `let client = SpiceClient::new((&config).into())?;`
but the explicit form is more readable.)

## Execution

### Step 1: API refactor (ConnectionConfig + SpiceClient signature change)

1. Add the `ConnectionConfig` struct definition to
   `shakenfist-spice-protocol/src/lib.rs`, after the existing
   re-exports. Add `pub use ConnectionConfig;` alongside the
   existing re-exports if defined in `lib.rs`, or define it in
   a new `connection.rs` file with `pub mod connection;` +
   `pub use connection::ConnectionConfig;` in `lib.rs`. (The
   `lib.rs` approach is simpler for a single struct; defer to
   a `connection.rs` file if the struct grows.)

2. In `ryll/src/protocol/client.rs`:
   - Change `use crate::config::Config;` to
     `use shakenfist_spice_protocol::ConnectionConfig;`.
   - Change `pub fn new(config: Config) -> Result<Self>` to
     `pub fn new(config: ConnectionConfig) -> Result<Self>`.
   - The `config` field type in the `SpiceClient` struct
     changes from `Config` to `ConnectionConfig`.
   - All field accesses (`self.config.host`,
     `self.config.port`, etc.) stay the same because
     `ConnectionConfig` has the same field names.

3. In `ryll/src/config.rs`:
   - Add `use shakenfist_spice_protocol::ConnectionConfig;`
   - Add `impl From<&Config> for ConnectionConfig` (the
     adapter shown above).

4. In `ryll/src/app.rs`:
   - Add `use shakenfist_spice_protocol::ConnectionConfig;`
   - At the `SpiceClient::new(config)?;` call site, change to
     `SpiceClient::new(ConnectionConfig::from(&config))?;`.

5. Verify locally:
   - `cargo fmt --all --check`
   - `cargo clippy --workspace --all-targets -- -D warnings`
   - `cargo test --workspace` — all 99 tests pass.
   - `pre-commit run --all-files`

6. Commit.

**Commit message:**
> Introduce ConnectionConfig for SpiceClient.

### Step 2: Move client.rs into the protocol crate

1. `git mv ryll/src/protocol/client.rs
   shakenfist-spice-protocol/src/client.rs`.
2. Delete `ryll/src/protocol/mod.rs` (`git rm`).
3. Remove `mod protocol;` from `ryll/src/main.rs`.
4. In `shakenfist-spice-protocol/src/lib.rs`:
   - Add `pub mod client;`.
   - Add `pub use client::SpiceClient;`.
5. In the moved `client.rs`, update the import:
   - `use shakenfist_spice_protocol::ConnectionConfig;`
     becomes `use crate::ConnectionConfig;` (since client.rs
     is now inside the protocol crate).
   - The `use shakenfist_spice_protocol::{...}` imports for
     `ChannelType`, `SpiceError` become `use crate::{...}`.
   - The `use shakenfist_spice_protocol::link::{...}` becomes
     `use crate::link::{...}`.
6. Add three new deps to
   `shakenfist-spice-protocol/Cargo.toml`:
   ```toml
   rustls-pemfile = "2"
   socket2 = { version = "0.6", features = ["all"] }
   webpki-roots = "0.26"
   ```
7. In `ryll/src/app.rs`:
   - Change `use crate::protocol::SpiceClient;` to
     `use shakenfist_spice_protocol::SpiceClient;`.
8. Verify locally (same suite as Step 1, plus check that
   `ryll/src/protocol/` is gone and `cargo build -p
   shakenfist-spice-protocol` succeeds).
9. Commit.

**Commit message:**
> Move SpiceClient into shakenfist-spice-protocol.

### Step 3: Mark Phase 6 complete

Update master plan execution table and record discoveries.

## Open questions

1. **Should `ConnectionConfig` be `#[non_exhaustive]`?**
   Decision for this phase: no. Struct-literal construction
   with `..Default::default()` is the natural pattern for a
   config struct. `#[non_exhaustive]` would force every
   external construction through a constructor or mutation,
   adding friction. Future `Option<T>` fields work fine with
   the existing struct literal pattern. Revisit at `0.1.0` if
   desired. See the "ConnectionConfig design" section above
   for the full rationale.

2. **Should ryll's `Config` be renamed now that
   `ConnectionConfig` exists?** The master plan (Decision #4)
   suggested considering a rename to `AppConfig` or
   `ClientConfig`. Since `Config` is used in 5+ places in
   ryll and the name is unambiguous within ryll's scope (it's
   the only `Config` type), renaming is churn for no real
   gain. Defer unless confusion arises.

3. **Should `host_subject` be included in ConnectionConfig
   even though SpiceClient doesn't use it?** Yes — Decision #4
   in the master plan already includes it. It's part of the
   .vv file spec, other clients may want it, and having it in
   ConnectionConfig means a future SpiceClient enhancement can
   start using it without a breaking change.

4. **Where does `ConnectionConfig` live — `lib.rs` or a
   new `connection.rs`?** For a single struct with no methods
   beyond `Debug/Clone/Default` derives, `lib.rs` is fine.
   If it grows a builder or validation logic, split it out
   then.

## Administration and logistics

### Success criteria

* `ryll/src/protocol/` no longer exists.
* `shakenfist-spice-protocol/src/client.rs` contains
  `SpiceClient` (moved from ryll).
* `shakenfist-spice-protocol/src/lib.rs` exports
  `ConnectionConfig`, `SpiceClient`, plus the existing
  constants/link/logging/messages modules.
* `ryll/src/config.rs` has `impl From<&Config> for
  ConnectionConfig`.
* `ryll/src/app.rs` imports `SpiceClient` and
  `ConnectionConfig` from `shakenfist_spice_protocol` and
  constructs `ConnectionConfig::from(&config)` at the call
  site.
* `mod protocol;` is gone from `ryll/src/main.rs`.
* All 99 tests pass (80 ryll + 2 compression + 17 usbredir +
  0 protocol).
* `cargo build -p shakenfist-spice-protocol` succeeds.
* CI is green.
* The crate's `Cargo.toml` still has `version = "0.0.0"`.

### Future work

* **Publish all three extracted crates as `0.1.0`** — the
  master plan's existing future-work item. With Phase 6
  done, all three crates have their final API shape (modulo
  the API polish items flagged in Phases 3-4).
* **Publish ryll itself to crates.io** — the separate
  future-work item from the ryll reservation commit.
* **Consider a builder pattern for ConnectionConfig** if the
  field count grows or if callers want fluent construction.
  Defer until a real consumer (kerbside) wants it.

### Bugs fixed during this work

(None expected.)

### Discoveries during execution

* **Doc-test added test count from 99 to 100.** The
  `ConnectionConfig` struct has a doc example in lib.rs with
  a runnable code block. This becomes a doc-test, adding 1
  to the total test count: 80 ryll + 2 compression + 17
  usbredir + 0 protocol lib tests + 1 protocol doc-test =
  100. This was not predicted by the plan (which said "99")
  but is correct and expected behaviour.

* **No surprises during the file move.** `client.rs`
  registered as 98% rename (2% from the `shakenfist_spice_protocol::*`
  → `crate::*` import change). `git log --follow` traces it
  back to `ryll/src/protocol/client.rs`.

* **Three new deps added cleanly.** `socket2`, `webpki-roots`,
  and `rustls-pemfile` were already in ryll's Cargo.toml with
  compatible versions. Adding them to the protocol crate
  required no version negotiation.

* **`ryll/src/protocol/` deleted entirely.** After Phase 6,
  ryll has no `protocol/` module at all. The `mod protocol;`
  declaration in `main.rs` is gone. `app.rs` imports
  `SpiceClient` directly from `shakenfist_spice_protocol`.

* **The `From<&Config> for ConnectionConfig` adapter is
  trivial** — 6 field clones with no logic. This confirms
  the research finding that ryll's `Config` was already
  minimal. If `ConnectionConfig` ever diverges from `Config`
  (e.g. adds a `proxy` field that Config doesn't have), the
  adapter is the one place to update.

### Back brief

Before executing, confirm:

1. Two-commit sequence: (Step 1) API refactor inside ryll +
   ConnectionConfig in protocol crate, (Step 2) move
   client.rs into protocol crate.
2. `ConnectionConfig` is NOT `#[non_exhaustive]`.
3. Three new deps (`socket2`, `webpki-roots`,
   `rustls-pemfile`) are added to the protocol crate in
   Step 2 (when client.rs moves), not Step 1.
4. `ryll/src/protocol/` is deleted entirely in Step 2.
5. No `version =` qualifier on path-deps.
6. Test count stays at 99.
