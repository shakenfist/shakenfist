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

## Open questions

1. **Workspace vs separate repos?** A Cargo workspace within the
   ryll repo is simplest for development (single CI, atomic
   commits across crate + consumer). Separate repos give cleaner
   versioning. The workspace approach is recommended for the
   initial extraction; we can split repos later if needed.

2. **Crate naming.** Options include:
   - `spice-decompression`, `spice-protocol`, `spice-usbredir`
     (generic, good for community reuse)
   - `ryll-decompression`, `ryll-protocol`, `ryll-usbredir`
     (tied to ryll, less reusable branding)
   The `spice-*` prefix is preferred since these are not
   ryll-specific.

3. **GLZ async dependency.** The GLZ decompressor is currently
   `async fn` because of the cross-channel retry loop (it awaits
   a tokio sleep). This means `spice-decompression` would need
   a tokio dependency. Options:
   - Accept the tokio dep (pragmatic, both ryll and kerbside
     use tokio).
   - Refactor GLZ to take a callback/trait for the dictionary
     lookup, removing the async requirement from the crate.
   - Move the retry logic into the caller and keep the crate
     sync.

4. **client.rs coupling.** `SpiceClient` in `protocol/client.rs`
   imports `crate::config::Config`. This needs refactoring before
   extraction: accept connection parameters directly rather than
   a Config struct. This can be deferred to a later phase.

5. **LZ4 decompression.** The LZ4 decoder lives in
   `src/channels/display.rs` (function `decompress_spice_lz4`)
   rather than in `src/decompression/`. It should be moved into
   the decompression module before or during extraction.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Convert ryll to a Cargo workspace | PLAN-crate-extraction-phase-01-workspace.md | Not started |
| 2. Extract spice-decompression crate | PLAN-crate-extraction-phase-02-decompression.md | Not started |
| 3. Extract spice-protocol crate | PLAN-crate-extraction-phase-03-protocol.md | Not started |
| 4. Extract spice-usbredir crate | PLAN-crate-extraction-phase-04-usbredir.md | Not started |
| 5. Refactor SpiceClient for extraction | PLAN-crate-extraction-phase-05-client.md | Not started |

### Phase 1: Convert ryll to a Cargo workspace

Set up the ryll repo as a Cargo workspace with `ryll` as the
initial (and only) member. Update CI and pre-commit to work with
the workspace layout. No functional changes to ryll.

### Phase 2: Extract spice-decompression

Create a new workspace member `spice-decompression` containing:
- `DecompressedImage` struct
- QUIC decoder (`quic.rs`)
- GLZ decoder (`glz.rs`)
- LZ decoder (`lz.rs`)
- LZ4 decoder (moved from `display.rs`)

Update ryll to `use spice_decompression::*` instead of the local
module. All existing tests must continue to pass.

### Phase 3: Extract spice-protocol

Create a new workspace member `spice-protocol` containing:
- Protocol constants and opcodes (`constants.rs`)
- Wire-format message structs (`messages.rs`)
- Protocol message name lookups (`logging.rs`)
- SPICE link handshake and TLS (`link.rs`)

Update ryll to depend on `spice-protocol`. The `client.rs` module
stays in ryll for now (it depends on Config).

### Phase 4: Extract spice-usbredir

Create a new workspace member `spice-usbredir` containing:
- USB/IP redirect constants
- Message types and serialisation
- Stream parser

Update ryll to depend on `spice-usbredir`.

### Phase 5: Refactor SpiceClient

Refactor `protocol/client.rs` to accept connection parameters
directly (host, port, TLS config, password) instead of importing
`Config`. This makes it eligible for inclusion in `spice-protocol`
or as a standalone `spice-client` crate. This phase can be
deferred until the kerbside rewrite needs it.

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
* The kerbside proxy can add `spice-decompression` and
  `spice-protocol` as git dependencies and use them without
  pulling in ryll's GUI, input, or channel management code.
* Documentation in each crate's `README.md` and `lib.rs`
  describes the public API.

### Future work

* **crates.io publishing** -- Once the APIs stabilise, publish
  the extracted crates so external projects can consume them
  via normal Cargo dependencies.
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
