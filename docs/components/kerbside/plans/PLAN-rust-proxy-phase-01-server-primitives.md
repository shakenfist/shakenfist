# Rust proxy phase 1: server-side SPICE primitives

Parent plan: `PLAN-rust-proxy.md`. This phase was planned at
high effort, as the master plan requires for protocol work.

## Prompt

This phase's implementation lands entirely in the
`shakenfist/ryll` repository (the `shakenfist-spice-protocol`
crate), but this plan lives in kerbside's `docs/plans/` per
the master plan. Sub-agents executing steps must be briefed
accordingly: read `/home/tars/src/shakenfist/ryll/AGENTS.md`
and `STYLEGUIDE.md` and follow ryll's conventions for code,
tests, commits, and pre-commit hooks. Ground all wire-format
decisions in the references below — do not guess. Where this
plan quotes exact byte layouts, they were verified against
both the Python implementation and the ryll source on
2026-07-04.

Reference implementations, in order of authority:

1. `kerbside/spiceprotocol/packets/linkmessages.py` —
   `ClientSpiceLinkMessPacket` is the exact server-role
   behaviour being ported (parse client link, generate RSA
   keypair, send reply). `kerbside/proxy.py`
   `ClientPassword` (line ~346) is the auth-side behaviour.
2. `shakenfist-spice-protocol/src/link.rs` — the existing
   client-role halves; new code must round-trip against
   them.
3. `/srv/src-reference/spice/spice-protocol/spice/protocol.h`
   and `/srv/src-reference/spice/spice/server/reds.cpp` —
   upstream ground truth (`reds_handle_new_link`,
   `reds_send_link_ack`, `reds_handle_ticket`).
4. `kerbside/docs/spice-link-protocol.md` — kerbside's own
   protocol documentation.

## Repository and branch logistics

All implementation for this phase happens in a dedicated
ryll worktree:

- Worktree: `/home/tars/src/shakenfist/ryll-wt-spice-server-primitives`
- Branch: `spice-server-primitives`, based on ryll's
  `develop` (created 2026-07-04 from commit `3176904`)
- Main clone (do not commit here):
  `/home/tars/src/shakenfist/ryll`

Sub-agents must do all work in the worktree, never the
main clone. The branch becomes a single ryll pull request
for the whole phase (one commit per step, per the master
plan's conventions). The operator creates and merges all
pull requests — never open one; pushing the branch is only
done when the operator asks.

## Situation

`shakenfist-spice-protocol` (371-line `link.rs`, 248-line
`client.rs`) implements only the client role:

- `SpiceLinkMess` has `serialize()` but no `parse()`.
- `SpiceLinkReply` has `parse()` but no `serialize()`.
- `encrypt_password()` exists (RSA-OAEP/SHA-1 of a
  NUL-terminated ticket, left-zero-padded to 128 bytes);
  there is no keypair generation and no
  `decrypt_password()`.
- `perform_link()` / `perform_auth()` drive the handshake
  from the client side only, and take `&mut SpiceStream`.
- `SpiceStream` is an enum of `Plain(TcpStream)` and
  `Tls(tokio_rustls::client::TlsStream<TcpStream>)` — the
  client TLS type is hardcoded, and the enum exposes only
  inherent `read_exact`/`write_all`/`flush` helpers, not
  the tokio `AsyncRead`/`AsyncWrite` traits.
- `SpiceCaVerifier` in `client.rs` hardcodes the
  `aws_lc_rs` provider for signature verification while
  the `ryll` and `shakenfist-spice-webrtc` binaries
  install `ring` as the process-default provider.
- `SpiceError::from_u32` exists; there is no `to_u32`.
- Errors are `anyhow` throughout; a server parsing
  untrusted input needs typed errors so callers can
  distinguish "malformed" from "needs redirect".
- There is no fuzzing anywhere in the ryll workspace.
- `SpiceStream` is consumed by `client.rs` and seven
  channel modules in `shakenfist-spice-renderer`; changes
  to it must not break the client.

## Mission

Add the server role to `shakenfist-spice-protocol` so a
proxy can terminate a SPICE client connection: parse an
inbound link message, reply (success or error), generate a
per-connection RSA keypair, decrypt the auth ticket, and
report the auth result — with the same wire bytes the
Python kerbside proxy produces today, typed errors,
DoS-resistant parsing, fuzz coverage, and no behaviour
change for the existing client role.

## Wire format reference

All integers little-endian unless noted.

**SpiceLinkHeader** (16 bytes, both directions):
`magic[4]='REDQ'`, `major:u32=2`, `minor:u32=2`,
`size:u32` = bytes following the header.

**SpiceLinkMess body** (client → server, 18 + 4·n bytes):
`connection_id:u32`, `channel_type:u8`, `channel_id:u8`,
`num_common_caps:u32`, `num_channel_caps:u32`,
`caps_offset:u32` (offset of the capability words from the
start of the body, 18 when they immediately follow), then
`num_common_caps + num_channel_caps` u32 capability words.
Python parse: `linkmessages.py:125-150`
(`'<4sIII'` then `'<IBBIII'` at offset 16). Rust
serialize: `link.rs:79-127`.

**SpiceLinkReply body** (server → client, 178 + 4·n
bytes): `error:u32` (`SpiceError`), `pub_key[162]` (DER
SubjectPublicKeyInfo of a 1024-bit RSA key with e=65537 —
exactly 162 bytes, which is why the field is fixed-size;
`SPICE_TICKET_PUBKEY_BYTES` in `protocol.h`),
`num_common_caps:u32`, `num_channel_caps:u32`,
`caps_offset:u32` (178 when the words immediately follow),
then the capability words. Ground-truth byte strings from
the Python proxy:

- Success (`linkmessages.py:201-205`):
  `struct.pack('<4sIIII162sIIIII', b'REDQ', 2, 2, 186, 0,
  der_pubkey, 1, 1, 178, common_caps, channel_caps)`.
  (Kerbside hardcodes `common_caps=11`,
  `channel_caps=9`, but that policy belongs to the
  proxy, not this crate — the crate API takes caller
  -supplied cap vectors.)
- `need_secured` redirect (`linkmessages.py:174-179`):
  `struct.pack('<4sIIII162sIII', b'REDQ', 2, 2, 178, 5,
  b'', 0, 0, 0)` — note the zeroed 162-byte key field and
  zero cap counts with `caps_offset=0`.

**Auth exchange** (after a success reply): client sends
`mechanism:u32` (must be 1, `AUTH_MECHANISM_SPICE`)
followed by a 128-byte RSA-OAEP blob; server replies
`error:u32` (0 = ok). OAEP uses SHA-1 as both the digest
and the MGF1 hash (`proxy.py:363-366`,
`link.rs:255-286`). The plaintext is the NUL-terminated
password; kerbside strips the final byte unconditionally
after decrypt (`[:-1]`, `proxy.py:366`). The rationale for
the NUL is documented in `link.rs:261-274`.

**Robustness limits for new parsers** (untrusted input;
none of these exist in the Python code but all are cheap):
reject `size` > 4096 in a link header; reject cap counts
whose combined byte length exceeds the declared `size`;
bounds-check `caps_offset` against the body before
indexing; parse and retain *all* capability words (the
Python code warns and ignores words beyond the first —
the Rust crate should not share that limitation).

These limits are not applied ad hoc: they are expressed
through a shared **bounded reader** (step 1a-prime) so the
invariants live in one tested place rather than being
re-derived per field. This is the phase's proving ground
for a systematic approach to wire-parser schema
validation across the workspace; the broader retrofit of
the pre-existing parsers, and a spike of declarative
binary-parsing crates (`binrw`/`deku`), are tracked
separately as shakenfist/ryll#136.

## Design decisions for this phase

- A shared **bounded reader** underpins the new parsers
  (see Robustness limits). It is a small newtype over a
  byte slice tracking a remaining-bytes budget, with
  `read_uN` helpers, `read_vec(count, max)` that rejects
  an oversized `count` *before* allocating,
  bounds-checked sub-slicing for offset-addressed fields,
  and typed errors that distinguish malformed from
  incomplete. The new server parsers are its first users;
  it is deliberately independent of the SPICE specifics
  so the pre-existing parsers can adopt it later
  (ryll#136).
- New server-role code lives in `link.rs` next to its
  client-role mirror images, not a new module — the pairs
  round-trip against each other and belong together.
- The handshake drivers become generic:
  `S: AsyncRead + AsyncWrite + Unpin + Send` replaces
  `&mut SpiceStream` so the same functions run over TCP,
  client TLS, server TLS, and `tokio::io::duplex` in
  tests. `SpiceStream` gains tokio `AsyncRead`/`AsyncWrite`
  impls (delegating) plus a
  `TlsServer(tokio_rustls::server::TlsStream<TcpStream>)`
  variant, so it satisfies the bound and existing callers
  keep working.
- The server auth driver is split, because the proxy must
  do a token lookup between decrypt and verdict:
  `read_link_mess(stream)`, `send_link_reply(stream, …)`,
  `send_need_secured(stream)`, `read_auth_ticket(stream,
  &key)`, and `send_auth_result(stream, SpiceError)` are
  separate functions the caller composes. (Contrast
  `perform_auth`, which can stay monolithic because the
  client knows its password upfront.)
- Typed errors: a `thiserror` `LinkError` enum (BadMagic,
  UnsupportedVersion, Truncated, TooLarge, BadCapsOffset,
  UnsupportedAuthMechanism, DecryptFailed, …) for the new
  parse paths; `anyhow` remains at the driver interface
  via `From`. Add `SpiceError::to_u32` as the inverse of
  `from_u32`.
- Crypto provider: stop hardcoding `aws_lc_rs` inside
  `SpiceCaVerifier`; use
  `rustls::crypto::CryptoProvider::get_default()` so the
  crate follows whatever provider the embedding process
  installed (ryll installs `ring`; kerbside-proxy will
  too).
- Fuzzing: a `fuzz/` crate (cargo-fuzz, nightly) with
  targets for `SpiceLinkMess::parse`,
  `SpiceLinkReply::parse`, and `read_auth_ticket` framing;
  CI builds all targets and smoke-runs each briefly on
  every PR. Long fuzz campaigns and coverage of the
  pre-existing parsers (messages.rs, the compression
  codecs, usbredir) are out of scope here and tracked as
  shakenfist/ryll#135.

## Open questions

- How kerbside-proxy will consume the crate (crates.io
  release vs git pin). Does not block this phase; the
  phase ends with a tagged version either way. Decide in
  phase 3 planning.
- Whether `send_link_reply` should also be adopted by a
  future ryll test double (a mock SPICE server for client
  tests). Out of scope here, but keep the API public and
  documented with that consumer in mind.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Generalize the stream layer in `shakenfist-spice-protocol`: add a `TlsServer(tokio_rustls::server::TlsStream<TcpStream>)` variant to `SpiceStream` (`link.rs:17-50`); implement `tokio::io::AsyncRead` and `AsyncWrite` for `SpiceStream` by delegating to the inner streams; change `perform_link` and `perform_auth` to take `stream: &mut S where S: AsyncRead + AsyncWrite + Unpin + Send` instead of `&mut SpiceStream` (their bodies already use only read_exact/write_all/flush, available via `AsyncReadExt`/`AsyncWriteExt`); update `client.rs` call sites. Keep the inherent helper methods so the seven `shakenfist-spice-renderer` channel modules compile unchanged — run the full workspace test suite to prove it. |
| 1a-prime | high | opus | none | Add a `reader` module to `shakenfist-spice-protocol` with a `BoundedReader` newtype over `&[u8]` (see Design decisions). API: construction with an explicit byte budget; `read_u8`/`read_u16`/`read_u32`/`read_u64` (little-endian, error on underrun); `read_bytes(n)` and a fixed `read_array::<N>()`; `read_vec_u32(count, max)` that returns `LinkError::TooLarge` if `count > max` *before* allocating; `sub_reader(offset, len)` and/or `slice_at(offset, len)` that bounds-check against the original buffer; and `remaining()`. All fallible methods return the `LinkError` thiserror enum (define it here: `Truncated`, `TooLarge`, `BadOffset`, plus room for the link/auth variants added in 1b/1d). No SPICE-specific knowledge in this module — it is generic wire-reading infrastructure so the parsers tracked in ryll#136 can adopt it later. Unit tests: underrun at each width, oversized-count rejection without allocation, out-of-bounds offsets, exact-fit success. This is the systematic home for the robustness limits; 1b/1c/1d consume it rather than open-coding bounds checks. |
| 1b | high | opus | none | Depends on 1a-prime. Implement `SpiceLinkMess::parse(data: &[u8]) -> Result<Self, LinkError>` in `link.rs` **using `BoundedReader`** (do not open-code bounds checks), extending the `LinkError` enum from 1a-prime with any link-specific variants (BadMagic, UnsupportedVersion) and adding `SpiceError::to_u32` in `constants.rs`. Parse the 16-byte header (validate magic `REDQ`, major=2; tolerate any minor) and the body per the Wire format reference, enforcing every robustness limit there (size cap 4096, cap-count/size consistency via `read_vec_u32`'s max, caps_offset bounds via `sub_reader`), and retaining all capability words in the existing `common_caps`/`channel_caps` Vec fields. Property: `parse(serialize(m))` round-trips for arbitrary valid messages — unit tests including multi-word caps, a byte-exact vector matching `linkmessages.py`'s documented layout, truncation at every boundary, and adversarial caps_offset values. This parser faces the internet; treat every offset as hostile. |
| 1c | medium | sonnet | none | Implement `SpiceLinkReply::serialize(&self) -> Vec<u8>` in `link.rs` as the exact mirror of the existing `parse` (`link.rs:142-208`), producing the byte layouts in the Wire format reference. The `pub_key` field is always written as exactly 162 bytes: zero-pad/zero-fill for error replies, error if a supplied key is not 162 bytes. Add a `SpiceLinkReply::error_reply(error: SpiceError)` constructor producing the need_secured-style form (zeroed key, zero caps, caps_offset 0). Unit tests: byte-exact vectors reproducing both Python `struct.pack` strings from the Wire format reference, and `parse(serialize(r))` round-trips including multi-word caps. |
| 1d | medium | sonnet | none | May reuse the `LinkError` enum from 1a-prime. Implement ticket-side crypto in `link.rs`: `generate_ticket_keypair() -> Result<(rsa::RsaPrivateKey, Vec<u8>)>` returning a fresh 1024-bit key (e=65537) and its DER SubjectPublicKeyInfo encoding, with a debug assertion/documented invariant that the DER is exactly 162 bytes; and `decrypt_password(key: &RsaPrivateKey, blob: &[u8; 128]) -> Result<String, LinkError>` doing RSA-OAEP with SHA-1 (digest and MGF1), then stripping one trailing NUL if present (kerbside strips the last byte unconditionally, `proxy.py:366`; be strict-but-compatible: strip iff NUL, else return the full string) and validating UTF-8. Tests: round-trip against the existing `encrypt_password` (encrypt→decrypt for empty, short, and 59-char passwords — SPICE_MAX_PASSWORD_LENGTH is 60 including NUL), and a fixed-key pinned vector. Use the `rsa`, `sha1`, `rand` crates already in the dependency tree. |
| 1e | high | opus | none | Implement the server-side handshake drivers in `link.rs`, generic over `S: AsyncRead + AsyncWrite + Unpin + Send` (from 1a): `read_link_mess(stream) -> Result<SpiceLinkMess>` (read header, validate, size-cap, read body, delegate to 1b's parse), `send_link_reply(stream, &SpiceLinkReply)`, `send_need_secured(stream)` (uses 1c's error_reply), `read_auth_ticket(stream, &RsaPrivateKey) -> Result<String>` (read u32 mechanism — error `UnsupportedAuthMechanism` unless 1 — then the 128-byte blob, delegate to 1d), and `send_auth_result(stream, SpiceError)`. Then write an end-to-end integration test over `tokio::io::duplex`: one task runs the existing client drivers (`perform_link`, `perform_auth`), the other runs the new server functions with a generated keypair, asserting the client authenticates successfully and the server recovers the exact password; plus a need_secured test asserting the client side surfaces `SpiceError::NeedSecured`. Update `lib.rs` module docs to describe the server role. |
| 1f | medium | sonnet | none | Reconcile the rustls crypto provider: in `client.rs` `SpiceCaVerifier` (lines ~67-101), replace the three hardcoded `aws_lc_rs::default_provider()` references with `rustls::crypto::CryptoProvider::get_default()` (handle the None case with a clear error telling the embedder to install a provider), and check the workspace for any other hardcoded provider references. Verify `ryll` still connects (existing tests / any TLS unit tests pass with the `ring` provider installed). Document in the crate docs that the embedding process must install a process-default provider. |
| 1g | medium | sonnet | none | Add cargo-fuzz to the ryll workspace: a `fuzz/` directory (per cargo-fuzz convention, likely under `shakenfist-spice-protocol/`) with targets `fuzz_link_mess_parse` (raw bytes → `SpiceLinkMess::parse`), `fuzz_link_reply_parse` (raw bytes → `SpiceLinkReply::parse`), and `fuzz_decrypt_password` (arbitrary 128-byte blobs against a fixed test keypair — assert no panic, errors are fine). Seed corpora from the byte-exact test vectors. Add a CI job to `.github/workflows/ci.yml` that installs nightly + cargo-fuzz, builds all targets, and smoke-runs each with `-max_total_time=30`; follow the existing workflow file's conventions and do not slow the main test job. |
| 1h | low | sonnet | none | Documentation and release: update `shakenfist-spice-protocol` crate docs and ryll's `ARCHITECTURE.md`/`AGENTS.md` to describe the server-role API and the fuzz setup; note the kerbside-proxy consumer. Bump the crate version per ryll's release conventions (cargo-release / `release.yml`) so kerbside can pin a version containing this work. Do not publish without the operator's go-ahead. |

Sequencing: 1a and 1a-prime are independent of each other
and can run first in parallel (1a generalizes the stream
layer; 1a-prime adds the bounded reader). 1b needs
1a-prime (it consumes `BoundedReader` and the `LinkError`
enum). 1c and 1d are independent of 1a-prime but 1d shares
`LinkError` with it, so land 1a-prime first to avoid enum
churn. 1e needs 1a + 1b + 1c + 1d. 1f is independent. 1g
needs 1b + 1c + 1d. 1h last.

Every step lands on the `spice-server-primitives` branch
in the ryll worktree (see Repository and branch
logistics) as one or more self-contained commits
referencing this plan
(`shakenfist/kerbside docs/plans/PLAN-rust-proxy-phase-01-server-primitives.md`),
passing ryll's pre-commit, `cargo fmt --check`,
`cargo clippy`, and the workspace test suite.

## Success criteria

* `parse`/`serialize` round-trip property tests pass for
  both link message and link reply, including multi-word
  capabilities.
* Byte-exact vectors matching the two Python
  `struct.pack` layouts (success and need_secured) pass.
* The duplex integration test proves the existing client
  drivers authenticate against the new server drivers,
  recovering the exact password including the NUL
  handling.
* All parsers of untrusted input have fuzz targets; CI
  builds and smoke-runs them on every PR; no panics.
* The full ryll workspace test suite passes unchanged —
  the client role has no behaviour change, including
  after the provider reconciliation.
* Typed `LinkError` values distinguish malformed input
  from protocol-level conditions; `SpiceError::to_u32`
  round-trips with `from_u32` for all defined codes.
* The new parsers apply their robustness limits through
  the shared `BoundedReader`, not open-coded per-field
  checks; the reader has its own unit tests for underrun,
  oversized-count rejection, and out-of-bounds offsets,
  and is free of SPICE-specific knowledge so the
  ryll#136 retrofit can adopt it.
* Crate docs describe the server role; a version is
  tagged that kerbside-proxy can pin in phase 3.

## Outcome

Completed 2026-07-04 on ryll branch `spice-server-primitives`
(worktree `/home/tars/src/shakenfist/ryll-wt-spice-server-primitives`),
ten commits `69c4c1e`..`ef01f57`, unmerged and unpushed pending
operator PR review. All steps validated in the ryll-dev container:
`cargo fmt --all --check`, `cargo clippy --workspace -- -D warnings`,
and `cargo test --workspace` (100 protocol-crate tests including the
duplex end-to-end handshake), plus a nightly `cargo fuzz build` and
30s smoke-run of all three targets.

Deviations from the plan, all deliberate:

* **No version bump (step 1h).** ryll uses `version.workspace =
  true` and an operator-driven whole-workspace release
  (`make propose-release X.Y.Z` → PR → `make tag-release`) from
  `develop`. Bumping on a feature branch would be wrong, so the
  release is deferred to that flow after this branch merges;
  phase 3 pins whichever release contains this work (or a git rev).
* **`SpiceLinkReply::parse` (existing client parser) was also
  rewritten onto `BoundedReader`.** The new `fuzz_link_reply_parse`
  target immediately found an unbounded `Vec::with_capacity` OOM in
  it (wire-controlled capability count). Fixed in commit `4bf56b5`
  with a regression test — this both unblocks a green fuzz CI job
  and makes an early down-payment on the ryll#136 retrofit. See
  Bugs below.
* **A fuzz-crate `cargo fmt --check` step was added to the CI job.**
  The detached fuzz workspace is not reached by the top-level lint
  job, so the fuzz job format-checks it directly (requires the
  `rustfmt` component on the nightly toolchain, added in the same
  job).

### Bugs fixed during this work

* **Unbounded allocation / OOM in `SpiceLinkReply::parse`**
  (shakenfist-spice-protocol, client-side reply parser). A
  capability count taken directly from the wire fed
  `Vec::with_capacity`, so a crafted reply (~4 billion words ≈ 16 GB)
  could abort the process. Found by the `fuzz_link_reply_parse`
  target added in this phase; fixed by rewriting the parser onto
  `BoundedReader` with the same size and cap-count limits as
  `SpiceLinkMess::parse` (commit `4bf56b5`, ryll branch). Pre-existing
  bug, not introduced here; it affected the ryll client too, so the
  fix benefits both consumers.

## Back brief

Before executing any step, back brief the operator on the
intended approach for that step and how it aligns with
this plan and the master plan.
