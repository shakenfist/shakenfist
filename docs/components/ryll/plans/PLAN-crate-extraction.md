# Extract reusable crates from ryll

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (module boundaries,
inter-module imports, trait usage, error handling), and ground
your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.
Where a question touches on external concepts (Cargo workspaces,
crates.io publishing, semver), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

When we get to detailed planning, I prefer a separate plan file
per detailed phase. These separate files should be named for the
master plan, in the same directory as the master plan, and simply
have `-phase-NN-descriptive` appended before the `.md` file
extension. Tracking of these sub-phases should be done via a table
in this master plan under the Execution section.

I prefer one commit per logical change, and at minimum one commit
per phase. Do not batch unrelated changes into a single commit.
Each commit should be self-contained: it should build, pass tests,
and have a clear commit message explaining what changed and why.

## Situation

Ryll is a pure-Rust SPICE client that includes several modules
which are self-contained protocol and algorithm implementations
with zero coupling to the application layer. A planned Rust
rewrite of the kerbside SPICE proxy will need many of the same
protocol types and decompression algorithms. Other developers
building SPICE tooling in Rust would also benefit from reusable
crates -- the only existing Rust SPICE crate (`spice-client`
v0.2.0 on crates.io) lacks QUIC/GLZ/LZ support and wraps limited
functionality.

An analysis of the ryll source tree identified three modules that
have **zero imports from the rest of ryll** and could be extracted
with minimal effort:

| Module | LOC | ryll imports | External deps |
|--------|-----|-------------|---------------|
| `src/decompression/` (QUIC, GLZ, LZ) | ~2,100 | None | tracing, byteorder, anyhow, tokio (GLZ async) |
| `src/protocol/` (constants, messages, logging, link) | ~1,600 | None (except client.rs -> Config) | tokio, tokio-rustls, byteorder, rsa, sha1 |
| `src/usbredir/` (parser, messages, constants) | ~1,300 | None | byteorder, anyhow |

## Mission and problem statement

Extract self-contained modules from ryll into reusable crates so
that:

1. The kerbside proxy rewrite can share protocol types and
   decompression code without vendoring or forking.
2. Other Rust SPICE projects can consume well-tested, pure-Rust
   implementations of SPICE-specific codecs and protocol types.
3. Ryll itself benefits from clearer module boundaries and the
   discipline of a public API.

The crates should live in the shakenfist organisation (either as
separate repos or as a Cargo workspace within ryll). They should
be publishable to crates.io but publishing is not required for
the initial extraction.

## Decisions

1. **Workspace, not separate repos.** The extracted crates will
   live as members of a Cargo workspace inside the ryll repo.
   Rationale:
   - Atomic refactors: changing a type in
     `shakenfist-spice-protocol` and updating ryll's call sites
     is one commit and one CI run, not a multi-repo version
     dance.
   - Shared `Cargo.lock` guarantees all members resolve to the
     same dependency versions.
   - Single CI pipeline and pre-commit config.
   - During development, ryll depends on the extracted crates
     via `path = "..."` — no version bumps needed to iterate.
   - All three crates will be released in lockstep by the same
     maintainer for the foreseeable future, so the "independent
     versioning" advantage of separate repos is theoretical.
   - If a crate later needs to split out (different maintainers,
     external contributors, different release cadence),
     `git filter-repo` can extract it with history preserved.

   **Publication is not blocked by the workspace layout.** Each
   workspace member publishes to crates.io independently via
   `cargo publish` from its own directory. Path dependencies are
   made publishable using the dual-spec idiom, which is the
   standard pattern used by tokio, serde, and most other
   multi-crate workspaces:

   ```toml
   shakenfist-spice-compression = { path = "../shakenfist-spice-compression", version = "0.1" }
   ```

   Cargo uses the path for local workspace builds and the version
   for published artifacts. Kerbside and other internal consumers
   can skip crates.io entirely and depend on the crates via git
   (`{ git = "...", rev = "..." }`), which is what the success
   criteria below already assume for the initial rollout.

   **Caveat (discovered during Phase 3):** the dual-spec
   idiom only works when the path-resolved version satisfies
   the version qualifier. While the extracted crates carry
   `0.0.0` placeholders in their local `Cargo.toml` to match
   the published Phase-2 reservations, ryll's path-dependency
   on each must be written **without** the `version =` field:

   ```toml
   shakenfist-spice-compression = { path = "../shakenfist-spice-compression" }
   ```

   The version qualifier is added back in the future-work
   commit that bumps each crate to `0.1.0` and publishes the
   real release. Phases 4 and 5 (protocol, usbredir) follow
   the same rule.

2. **GLZ async / tokio dependency: accept it.** The GLZ
   decompressor is currently `async fn` because of the
   cross-channel retry loop (it awaits a tokio sleep), so
   `shakenfist-spice-compression` will pull in tokio as a
   dependency. Rationale:
   - Both ryll and the planned kerbside rewrite already use
     tokio, so neither consumer pays a real cost.
   - Refactoring GLZ to take a callback/trait for the dictionary
     lookup (or hoisting the retry loop into the caller) would
     change the API shape of the only piece of the decompression
     crate that actually needs cross-channel coordination, and
     would do so purely to satisfy a hypothetical sync consumer
     that does not exist.
   - tokio is the de facto standard async runtime in the Rust
     ecosystem; depending on it does not meaningfully reduce
     the crate's reusability.
   - If a future sync-only consumer ever turns up, the API can
     be revisited then — the decompression code itself is
     small enough that the refactor would not be expensive.

3. **Crate naming: `shakenfist-spice-*` prefix.** The extracted
   crates will be named:
   - `shakenfist-spice-compression`
   - `shakenfist-spice-protocol`
   - `shakenfist-spice-usbredir`

   Rationale:
   - crates.io has a flat namespace with no organisation or
     scoping syntax (no `@org/crate` like npm). The community
     convention for expressing ownership is a project/org prefix
     on the crate name, e.g. `tokio-postgres`, `aws-sdk-s3`,
     `serde_json`.
   - Naming the crates `ryll-*` would be confusing for kerbside
     and other non-ryll consumers, since the crates are not
     ryll-specific code — they are SPICE protocol and codec
     implementations that ryll happens to be the first user of.
   - Bare `spice-*` names raise two concerns. First, SPICE is a
     Red Hat trademark, and using it as the primary identifier
     of a crate (rather than in a descriptive README) carries
     trademark exposure and risks being read as an official or
     endorsed implementation. Second, publishing bare `spice-*`
     names would block Red Hat or the upstream SPICE project
     from using those names on crates.io in the future, which
     would be a poor neighbour move even absent any legal risk.
   - The `shakenfist-` prefix clearly signals "this is a
     shakenfist-maintained implementation" and leaves the bare
     `spice-*` namespace available for upstream or other
     community crates. It still contains "spice" so the crates
     remain discoverable by anyone searching for SPICE tooling
     on crates.io.
   - Each crate's README and `Cargo.toml` description will
     clearly describe the crate as "a pure-Rust implementation
     of the SPICE <component>, extracted from ryll," which is
     nominative fair use of the SPICE trademark.

4. **SpiceClient takes a `ConnectionConfig` struct, not a
   `ryll::Config`.** `SpiceClient::new` currently imports
   `crate::config::Config`, which blocks extraction. The fix is
   to introduce a narrow `ConnectionConfig` struct in
   `shakenfist-spice-protocol` containing only the fields the
   protocol crate actually needs:

   ```rust
   #[derive(Debug, Clone, Default)]
   pub struct ConnectionConfig {
       pub host: String,
       pub port: u16,
       pub tls_port: Option<u16>,
       pub password: Option<String>,
       pub ca_cert: Option<String>,
       pub host_subject: Option<String>,
   }
   ```

   Rationale:
   - Keeps the broader "how did the user configure this session"
     concerns (CLI args, .vv file parsing, headless mode, USB
     disks, cadence, capture, monitor count, etc.) inside ryll,
     which mirrors the division of labour in the previous Python
     spice-gtk / virt-viewer implementation.
   - Gives the protocol crate a narrow, documented public API
     surface for "what do I need to dial a SPICE server" without
     dragging in ryll-specific application concerns.
   - Avoids name clashes: ryll keeps its own `Config` type for
     the broader app config; the protocol crate has
     `ConnectionConfig`. The two are distinct concepts.
   - Other consumers (kerbside, third-party tools) won't have
     `.vv` files or clap-based CLI parsing; they'll build a
     `ConnectionConfig` from whatever config source they use.
   - A struct is strictly better than a multi-argument
     `SpiceClient::new(host, port, tls_port, password, ca_cert,
     host_subject)` constructor: six positional arguments, most
     of them `Option`, is exactly the shape that produces
     site-of-call bugs. A struct lets callers use
     `ConnectionConfig { host, port, ..Default::default() }`
     and only mention the fields they care about, and adding
     fields later is a non-breaking change.

   Ryll's `Config` (kept in ryll) will expose an adapter — e.g.
   `impl From<&ryll::Config> for ConnectionConfig` or a
   `to_connection_config()` method — as the one place where the
   broader app config is narrowed to protocol connection params.
   All `.vv` parsing, URL fetching, and `--direct` command-line
   handling stays in ryll; the protocol crate gets no
   `configparser` or `reqwest` dependency.

5. **Message types in `messages.rs` become real structs.**
   Several message types in `src/protocol/messages.rs` are
   currently zero-sized marker structs with associated
   `write(positional, args, ...)` functions, e.g.:

   ```rust
   pub struct MousePosition;
   impl MousePosition {
       pub fn write(x: u32, y: u32, buttons: u32, display_id: u8,
                    buf: &mut Vec<u8>) -> io::Result<()> { ... }
   }
   ```

   This has two problems for a public API: `(x, y, buttons,
   display_id)` are all the same primitive type so positional
   calls are error-prone, and the "struct" carries no data so
   it can't be constructed, logged, or passed around
   independently of serialization. During Phase 4 these types
   will be refactored into real structs with fields and a
   `write(&self, buf)` method:

   ```rust
   pub struct MousePosition {
       pub x: u32,
       pub y: u32,
       pub buttons: u32,
       pub display_id: u8,
   }
   impl MousePosition {
       pub fn write(&self, buf: &mut Vec<u8>) -> io::Result<()> { ... }
   }
   ```

   The same refactor applies to `KeyEvent`, `MouseButton`, and
   any other message type following the marker-struct pattern,
   for consistency. This is an API cleanup only — the wire
   format is unchanged.

6. **Single `shakenfist-spice-compression` crate with
   feature-gated algorithms.** All four SPICE image codecs
   (QUIC, GLZ, LZ, LZ4) live in one crate rather than one crate
   per algorithm. Rationale:
   - `DecompressedImage` is the shared return type for all four
     decoders. In a one-crate-per-algorithm layout it would have
     to live in a fifth "types" crate, be duplicated, or be
     hoisted into `shakenfist-spice-protocol`. All three are
     uglier than keeping it next to the decoders.
   - Real SPICE clients dispatch on an image type byte and need
     all four decoders; the "only depend on QUIC" use case is
     theoretical for the current consumer set (ryll, kerbside).
   - ~2,100 LOC is small. Splitting it into four crates is
     over-engineering — more `Cargo.toml`s, more README files,
     more lockstep version bumps, more crates.io names to
     defend.
   - The only real per-algorithm dependency difference is that
     GLZ needs tokio (for the async cross-channel retry loop).
     Cargo feature flags handle this cleanly.

   **The crate is named "compression", not "decompression",**
   even though the initial `0.1.0` release will only contain
   decompression code (matching what ryll has today). The
   broader name reserves namespace for future compression
   implementations of the same codecs, which a SPICE proxy
   (such as the planned Rust rewrite of kerbside) or
   server-side tooling will eventually want. A single crate
   that covers both directions is more convenient than
   splitting compression and decompression across two crates,
   and the alternative — renaming the crate later — would be
   a breaking change to every consumer's `Cargo.toml`.

   The crate will use feature flags to let consumers opt out of
   algorithms they don't need (and their transitive deps):

   ```toml
   # shakenfist-spice-compression/Cargo.toml
   [features]
   default = ["quic", "glz", "lz", "lz4"]
   quic = []
   glz = ["dep:tokio"]
   lz = []
   lz4 = ["dep:lz4_flex"]  # or whichever LZ4 crate we pick

   [dependencies]
   byteorder = "1"
   anyhow = "1"
   tracing = "0.1"
   tokio = { version = "1", features = ["sync", "time"], optional = true }
   lz4_flex = { version = "0.11", optional = true }
   ```

   And each decoder module is `#[cfg(feature = "...")]`-gated in
   `lib.rs`. Ryll and kerbside depend with default features;
   hypothetical QUIC-only consumers can set `default-features =
   false, features = ["quic"]` and pay nothing for tokio. Once
   compression code is added, additional features (e.g.
   `quic-encode`, `lz4-encode`) may be introduced to gate the
   per-direction code, but the existing decoder features
   remain stable.

   This decision should be revisited if one of the decoders
   grows beyond ~5k LOC, if genuine non-SPICE users of a single
   algorithm appear, or if the feature matrix becomes painful to
   test.

7. **Reserve crate names on crates.io early.** As soon as the
   workspace exists, publish minimal `0.0.0` placeholder crates
   for all three names:
   - `shakenfist-spice-compression`
   - `shakenfist-spice-protocol`
   - `shakenfist-spice-usbredir`

   Rationale:
   - crates.io has a flat, immutable, first-come namespace.
     Names cannot be reclaimed except via a trademark or
     squatting complaint to the Rust Foundation, which is slow
     and not guaranteed.
   - The risk is asymmetric: reservation costs almost nothing,
     while losing the names — to a typosquatter, an
     AI-generated junk crate, or even a well-meaning third
     party — would be a significant setback we'd have to either
     work around or fight to undo.
   - The Rust Foundation's crates.io policy explicitly allows
     reserving a name for an in-progress project, provided the
     placeholder clearly identifies the reservation, the
     reserving party, and links to the public work-in-progress.
     Pure squatting (reserving with no intent to ship) is not
     allowed; we have clear intent.
   - Publishing `0.0.0` is the conventional placeholder version.
     Cargo's resolver will prefer any later `0.1.x` release, so
     the placeholder does not interfere with real consumers.
     Once `0.1.0` ships in a later phase, the `0.0.0` placeholder
     can optionally be yanked.

   Each placeholder crate will contain:
   - A minimal `Cargo.toml` with the required metadata
     (`name`, `version = "0.0.0"`, `license`, `description`,
     `repository`, `authors`).
   - A one-line `lib.rs` (`//! Reserved name; see README.`).
   - A `README.md` that says clearly: "This crate name is
     reserved by shakenfist for an upcoming pure-Rust SPICE
     <component> implementation extracted from the ryll SPICE
     client. The real `0.1.0` release will follow when the
     extraction lands. See <link to ryll repo and the extraction
     plan>."

   The placeholder publish happens in a dedicated phase
   (Phase 2 below), immediately after the workspace exists and
   before any real extraction work begins.

## Open questions

1. **Other multi-argument APIs (minor).** A few other public
   functions in the to-be-extracted modules take 4-5 arguments
   but are not obvious refactor candidates:
   - `SpiceLinkMess::new` (5 args) — the struct already has
     `pub` fields, so callers can use struct literal syntax
     directly and the constructor could be removed or reduced.
   - `perform_link` (4 args: stream + channel id triple) — could
     introduce a `ChannelId { connection_id, channel_type,
     channel_id }` struct since the same triple flows through
     `SpiceClient::connect_channel` → `perform_link` →
     `SpiceLinkMess`. Minor ergonomic win.
   - `log_message` / `log_unknown` (5 args each) — utility
     logging functions; allocating a struct per log line would
     be worse than the current shape. Leave alone.

   Decide on these case-by-case during Phase 4; none of them
   block the extraction.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Convert ryll to a Cargo workspace | [PLAN-crate-extraction-phase-01-workspace.md](/components/ryll/plans/PLAN-crate-extraction-phase-01-workspace/) | Complete |
| 2. Reserve crate names on crates.io | [PLAN-crate-extraction-phase-02-reserve-names.md](/components/ryll/plans/PLAN-crate-extraction-phase-02-reserve-names/) | Complete |
| 3. Extract shakenfist-spice-compression crate | [PLAN-crate-extraction-phase-03-compression.md](/components/ryll/plans/PLAN-crate-extraction-phase-03-compression/) | Complete |
| 4. Extract shakenfist-spice-protocol crate | [PLAN-crate-extraction-phase-04-protocol.md](/components/ryll/plans/PLAN-crate-extraction-phase-04-protocol/) | Complete |
| 5. Extract shakenfist-spice-usbredir crate | [PLAN-crate-extraction-phase-05-usbredir.md](/components/ryll/plans/PLAN-crate-extraction-phase-05-usbredir/) | Complete |
| 6. Introduce ConnectionConfig and move SpiceClient into protocol crate | [PLAN-crate-extraction-phase-06-client.md](/components/ryll/plans/PLAN-crate-extraction-phase-06-client/) | Complete |

### Phase 1: Convert ryll to a Cargo workspace

Set up the ryll repo as a Cargo workspace with `ryll` as the
initial (and only) member. Update CI and pre-commit to work with
the workspace layout. No functional changes to ryll.

### Phase 2: Reserve crate names on crates.io

Per Decision #7, reserve all three crate names on crates.io as
soon as the workspace exists, before any extraction work begins.
This phase is small and self-contained but time-sensitive — it
should ship promptly to minimise the squatting window.

For each of `shakenfist-spice-compression`,
`shakenfist-spice-protocol`, and `shakenfist-spice-usbredir`:

1. Create a new workspace member directory with a minimal
   `Cargo.toml` (`name`, `version = "0.0.0"`, `license`,
   `description`, `repository`, `authors`).
2. Add a one-line `lib.rs` (`//! Reserved name; see README.`).
3. Add a `README.md` clearly stating that the crate name is
   reserved by shakenfist for an upcoming pure-Rust SPICE
   <component> implementation extracted from the ryll SPICE
   client, with a link back to the ryll repo and to this
   extraction plan.
4. Verify the crate builds with `cargo build -p <name>`.
5. Publish with `cargo publish -p <name>` (requires `cargo
   login` first).

The workspace will then have three placeholder members alongside
ryll. Subsequent extraction phases will replace each placeholder
in-place with the real code, bumping the version to `0.1.0` at
publish time. The `0.0.0` placeholders can optionally be yanked
once their `0.1.0` replacements are live, but yanking is not
required since the resolver will prefer `0.1.0` automatically.

This phase makes no changes to ryll itself.

### Phase 3: Extract shakenfist-spice-compression

Note: per Decision #6, the crate is named "compression" even
though the initial `0.1.0` release will only contain
**decompression** code (matching ryll's current scope). The
broader name reserves namespace for compression
implementations of the same codecs in future minor releases.

Replace the `0.0.0` placeholder created in Phase 2 with the
real implementation, containing:
- `DecompressedImage` struct (always available)
- QUIC decoder (`quic.rs`, feature `quic`)
- GLZ decoder (`glz.rs`, feature `glz`, pulls in tokio)
- LZ decoder (`lz.rs`, feature `lz`)
- LZ4 decoder (feature `lz4`)

Per Decision #6, the crate uses Cargo feature flags to gate each
algorithm, with all four enabled by default. Consumers who only
need a subset can disable default features and opt in to the
ones they need.

As a prerequisite to the extraction, move the LZ4 decoder out of
`ryll/src/channels/display.rs` (function `decompress_spice_lz4`)
and into `ryll/src/decompression/lz4.rs` alongside the other
decoders. LZ4 is a general-purpose SPICE image codec and does
not belong in the display channel implementation; it was only
there for historical reasons. This move should be a standalone
commit that lands before the crate extraction begins, so the
"create new crate and move files" step stays mechanical.

Update ryll to `use shakenfist_spice_compression::*` instead of
the local module. All existing tests must continue to pass.

### Phase 4: Extract shakenfist-spice-protocol

Create a new workspace member `shakenfist-spice-protocol`
containing:
- Protocol constants and opcodes (`constants.rs`)
- Wire-format message structs (`messages.rs`)
- Protocol message name lookups (`logging.rs`)
- SPICE link handshake and TLS (`link.rs`)

As part of this phase, also refactor the marker-struct message
types in `messages.rs` (`MousePosition`, `KeyEvent`,
`MouseButton`, etc.) into real structs with `pub` fields and a
`write(&self, buf)` method, per Decision #5. This is an API
cleanup before the types become public; the wire format is
unchanged.

Update ryll to depend on `shakenfist-spice-protocol`. The
`client.rs` module stays in ryll for now — it is moved in
Phase 6 once the `ConnectionConfig` refactor lands.

### Phase 5: Extract shakenfist-spice-usbredir

Create a new workspace member `shakenfist-spice-usbredir`
containing:
- USB/IP redirect constants
- Message types and serialisation
- Stream parser

Update ryll to depend on `shakenfist-spice-usbredir`.

### Phase 6: Refactor SpiceClient and move into protocol crate

Introduce a `ConnectionConfig` struct in
`shakenfist-spice-protocol` containing only the fields the
protocol layer needs (host, port, tls_port, password, ca_cert,
host_subject), per Decision #4. Refactor
`protocol/client.rs::SpiceClient` to take a `ConnectionConfig`
instead of importing `crate::config::Config`, then move
`client.rs` into the `shakenfist-spice-protocol` crate.

In ryll, keep the existing `Config` type (renamed or left as-is)
as the application-wide configuration that owns CLI parsing, .vv
file handling, and all the broader session settings (headless,
cadence, USB disks, share dirs, monitors, capture, etc.). Add an
adapter — `impl From<&ryll::Config> for ConnectionConfig` or a
`Config::to_connection_config()` method — as the one place where
the broader app config is narrowed to protocol connection
parameters.

Kerbside and other consumers will construct `ConnectionConfig`
values from whatever configuration source they use; they do not
need ryll's `Config` type or .vv file parsing.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Ryll builds and all 99+ tests pass using the workspace layout.
* Each extracted crate compiles independently with `cargo build`
  and `cargo test` from its own directory.
* Each crate has no `use crate::` imports pointing at ryll
  application code.
* CI runs tests for all workspace members.
* The kerbside proxy can add `shakenfist-spice-compression`
  and `shakenfist-spice-protocol` as git dependencies and use
  them without pulling in ryll's GUI, input, or channel
  management code.
* Documentation in each crate's `README.md` and `lib.rs`
  describes the public API.

### Future work

* **crates.io `0.1.0` publishing** -- Names are reserved with
  `0.0.0` placeholders in Phase 2. Once the APIs stabilise,
  publish `0.1.0` releases of each crate (which the resolver
  will prefer over the placeholder) so external projects can
  consume them via normal Cargo dependencies. Optionally yank
  the `0.0.0` placeholders after the real releases land.
* **First proper crates.io release of ryll itself** -- The
  `ryll` crate name was reserved on crates.io alongside the
  three `shakenfist-spice-*` placeholders, but as a one-off
  `0.0.0` publish from a temporary directory outside the
  workspace (because the workspace already has a `ryll`
  member with the same name). The real first publish needs:
  - `keywords` (max 5, lowercase, alphanumeric+hyphen) and
    `categories` (from the official crates.io list) added to
    `ryll/Cargo.toml`.
  - `homepage`, `documentation`, and explicit `readme` fields.
  - A dependency review to confirm every transitive dep
    publishes cleanly (no path-only deps without a version
    pin, no git deps).
  - A decision about whether the default `capture` feature
    set is too heavy for `cargo install ryll` users on a
    minimal system, and whether the GUI dependencies (eframe,
    X11/Wayland/GL dev packages) need a more useful "missing
    system dep" error message.
  - A release-workflow step that publishes `ryll` to crates.io
    alongside the existing GitHub release artefacts, so future
    tags don't drift between the two distribution channels.
  - A version bump from `0.1.3` to `0.1.4` (or higher) for
    the first real release, since `0.0.0` is already taken by
    the placeholder.

  This work is deliberately deferred to keep the crate
  extraction plan focused; the placeholder removes the
  squatting risk in the meantime.
* **WebDAV mux extraction** -- The `src/webdav/mux.rs` module is
  also self-contained and could become a crate if other projects
  need SPICE WebDAV channel support.
* **Fuzz testing** -- Extracted crates with well-defined input
  boundaries (byte slices) are ideal targets for cargo-fuzz.
  The QUIC decoder in particular should be fuzz-tested.

### Bugs fixed during this work

(None yet.)

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
