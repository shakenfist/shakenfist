# Host subject phase 1: subject pinning in shakenfist-spice-protocol

This is the detailed plan for phase 1 of
[PLAN-host-subject.md](/components/kerbside/plans/PLAN-host-subject/). Read the
master plan first; its Situation and Design decisions
sections are assumed knowledge here.

## Prompt

All implementation for this phase lands in the
`shakenfist/ryll` repository (checkout at
`/home/tars/src/shakenfist/ryll`), in the
`shakenfist-spice-protocol` crate. The plan itself lives
here in `shakenfist/kerbside/docs/plans/` per the master
plan's cross-repo convention; sub-agents must be briefed
accordingly.

Before implementing, ground every semantic claim in the
reference sources rather than this plan's paraphrase of
them: the canonical parser and comparison are
`subject_to_x509_name` (`ssl_verify.c:282-372`) and
`verify_subject` (`ssl_verify.c:374-425`) in
`/srv/src-reference/spice/spice-common/common/ssl_verify.c`.
Consult ryll's `STYLEGUIDE.md` and `AGENTS.md` for crate
conventions, and run quality gates through the Makefile
(they wrap builds in Docker, per the operator's preference
for containerised Rust): `make lint` (rustfmt + clippy
`-D warnings`) and `make test` (workspace tests).

## Repository and branch logistics

- Repo: `shakenfist/ryll`, branch `host-subject-verifier`
  off current `develop`.
- One commit per logical change (see Execution); each
  commit must pass `make lint` and `make test` on its
  own.
- Landing this phase means merging to ryll `develop`.
  Kerbside consumes the crate as a git dependency pinned
  by `rev`, so nothing in kerbside changes until phase 2
  bumps the pin. Note: ryll's
  `docs/plans/PLAN-crate-release.md` will eventually put
  these crates on crates.io (currently parked at 0.0.0);
  until then the git pin remains the release mechanism.

## Situation

See the master plan. Crate-local facts verified
2026-07-17:

- `ConnectionConfig` (`shakenfist-spice-protocol/src/lib.rs:80-94`)
  already has `host_subject: Option<String>` documented as
  "informational only". Ryll's `.vv`/INI parsing already
  populates it (`ryll/src/config.rs:217,243,369`), and
  `docs/configuration.md:144` documents the `host-subject`
  key as "Expected server certificate subject" — without
  mentioning that nothing checks it.
- `SpiceCaVerifier` (`src/client.rs:33-113`) delegates to
  rustls's `WebPkiServerVerifier`, then downgrades
  `NotValidForName` / `NotValidForNameContext` to
  acceptance when a custom CA is in use
  (`client.rs:47-78`). It is only installed when a custom
  CA is configured (`client.rs:161-185`); otherwise a
  stock rustls config (system roots, full hostname
  verification) applies.
- The crypto provider comes from the process-wide rustls
  default (`client.rs:167-176`); both ryll and the
  kerbside proxy install `ring`.
- Crate deps are declared per-crate (not workspace
  tables): `tokio-rustls = "0.26"`, `tracing = "0.1"`,
  `thiserror = "2"`, `webpki-roots = "1.0"`; dev-deps
  only `tokio`. `x509-parser` is not a direct dep but is
  already in the workspace lockfile twice (0.16 via the
  webrtc stack's `dtls`, 0.18.1 via `rcgen 0.14.8`).
  `rcgen 0.14` is already a dev-dep of the `ryll` crate
  ("Self-signed cert generation for the --web TLS
  integration tests") and of kerbside's proxy.
- The fuzz crate (`shakenfist-spice-protocol/fuzz/`) is a
  detached workspace with three `[[bin]]` targets; ryll's
  CI builds each via a matrix at
  `.github/workflows/ci.yml:81`
  (`target: [fuzz_link_mess_parse, fuzz_link_reply_parse,
  fuzz_decrypt_password]`) as a compile gate on nightly.
- There is no `CHANGELOG.md`; release notes are GitHub
  Releases cut from tags. Behaviour changes are recorded
  in commit messages and the affected docs.
- `client.rs` currently has no `#[cfg(test)]` module; the
  crate's test precedent is sync parse tests plus async
  `tokio::io::duplex` handshake tests in `link.rs`.

## Mission

Teach the crate to enforce `host_subject` when set:
`SpiceClient::new` rejects malformed expected-subject
strings, and the TLS handshake fails unless the backend's
end-entity certificate subject matches under spice-common
semantics. `host_subject: None` behaves exactly as today.

## Design decisions

1. **New module `src/host_subject.rs`** owning the whole
   feature: a parsed-representation type, the string
   parser, and the DER comparison. Public API (pub so the
   fuzz target and the verifier can use it; also the
   natural unit-test surface):
   - `pub struct ExpectedSubject` — ordered
     `Vec<(SubjectAttr, String)>` of parsed entries,
     values stored normalised (decision 3).
   - `pub fn parse_host_subject(s: &str)
     -> Result<ExpectedSubject, HostSubjectError>`
   - `impl ExpectedSubject { pub fn matches_cert_der(
     &self, der: &[u8]) -> Result<(), HostSubjectError> }`
     returning a descriptive error on mismatch (used for
     the log line), `Ok(())` on match.
   - `HostSubjectError` via `thiserror` (crate
     precedent), with variants distinguishing parse
     errors from mismatch/undecodable-certificate errors.
2. **Parser semantics** replicate
   `subject_to_x509_name` exactly:
   - Entries are `key=value` separated by `,`.
   - Backslash escapes exactly `\` and `,`; a backslash
     before any other character is a parse error. An
     escaped comma is a literal within the value.
   - Spaces immediately before a key are skipped; all
     other whitespace is literal.
   - A `,` before any `=` in the entry is an error
     ("assignment is missing"); an empty value is an
     error; a trailing empty entry (trailing comma)
     terminates cleanly.
   - Keys are case-sensitive OpenSSL short names from
     the fixed set `C`, `ST`, `L`, `O`, `OU`, `CN`,
     `DC`, `emailAddress`, mapped to their DER OIDs
     (2.5.4.6, 2.5.4.8, 2.5.4.7, 2.5.4.10, 2.5.4.11,
     2.5.4.3, 0.9.2342.19200300.100.1.25,
     1.2.840.113549.1.9.1). Any other key is a parse
     error — fail closed. This settles the master plan's
     open question: the set covers every subject any
     SPICE deployment in our ecosystem produces (oVirt,
     qemu's documented cert recipes, our CI) plus DC for
     AD-issued certs; growing it later is a
     compatible change.
3. **Comparison semantics** approximate `verify_subject`
   + `X509_NAME_cmp` canonical form:
   - Parse the end-entity certificate with
     `x509_parser::certificate::X509Certificate::from_der`
     and flatten the subject's
     AttributeTypeAndValue entries in order (iterate
     RDNs, then AVAs within each RDN).
   - The AVA count must equal the expected entry count.
   - Pairwise, in order: attribute OID must equal the
     expected key's OID, and values must be equal after
     normalisation: decode the DER string (UTF8String /
     PrintableString / IA5String via x509-parser's
     `as_str()`), trim leading/trailing whitespace,
     collapse internal whitespace runs to a single
     space, and compare ASCII case-insensitively.
     Unicode case folding is deliberately out of scope
     (documented); an undecodable value type
     (T61String, BMPString) is a mismatch — fail
     closed.
4. **Verifier wiring** (`src/client.rs`):
   - `SpiceCaVerifier` gains
     `expected_subject: Option<ExpectedSubject>`.
   - In `verify_server_cert`, run the subject check on
     `end_entity` after chain verification succeeds —
     in both the clean-Ok path and the
     NotValidForName-relaxed path. On mismatch, emit
     `tracing::warn!` naming the expected string and the
     presented subject, and return
     `Error::InvalidCertificate(CertificateError::NotValidForName)`.
   - `create_tls_connector` installs the custom verifier
     when a custom CA is configured **or**
     `host_subject` is set (root store construction is
     unchanged: system roots plus optional custom CA).
     With a subject pinned, subject verification
     substitutes for hostname verification exactly as in
     spice-gtk.
   - `SpiceClient::new` parses `config.host_subject`
     eagerly and returns the parse error at construction
     time, so a malformed pin never silently downgrades
     to an unpinned connection and ryll users see the
     error before any connect attempt.
5. **Dependencies**: add `x509-parser = "0.18"` to
   `[dependencies]` and `rcgen = "0.14"` to
   `[dev-dependencies]` of the protocol crate. Both
   already resolve in the workspace lockfile; run
   `cargo deny check` (a `deny.toml` exists at the
   workspace root) to confirm no licence/advisory
   surprises.
6. **Fuzzing**: new `fuzz_parse_host_subject` target
   feeding arbitrary bytes (lossy UTF-8) to
   `parse_host_subject`, registered in
   `fuzz/Cargo.toml` (`[[bin]]` with `test = false,
   doc = false, bench = false`, matching the existing
   three) and added to the CI fuzz matrix at
   `.github/workflows/ci.yml:81`.
7. **Documentation of the behaviour change**: update the
   `lib.rs` doc comments for `ca_cert` (`lib.rs:88-90`,
   hostname relaxation now conditional on no pinned
   subject) and `host_subject` (`lib.rs:91-93`, no
   longer informational); update ryll
   `docs/configuration.md:144` and the `.vv` example at
   `:187`; update the crate `README.md` if it describes
   TLS behaviour; state the behaviour change prominently
   in the wiring commit's message (a `.vv` with a stale
   `host-subject` now fails to connect) so it lands in
   the next release's notes.

## Open questions (to settle during the phase)

- Whether `x509_parser`'s `as_str()` covers IA5String
  (needed for `emailAddress`); if not, decode it
  explicitly. The implementing agent must check the
  crate's API rather than assume.
- Whether multi-AVA RDNs need distinct handling in the
  flatten step (spice-common counts AVAs via
  `X509_NAME_entry_count`, which also flattens). Match
  spice-common: flatten and compare pairwise. Confirm
  against `ssl_verify.c` while implementing.
- Whether `make lint`'s clippy gate wants the new module
  exported from `lib.rs` behind `pub mod host_subject`
  or re-exported types at the crate root (follow the
  crate's existing style for `link`/`messages`).

## Key facts front-loaded for the sub-agents

- Verifier struct and impl: `src/client.rs:33-113`;
  connector construction: `src/client.rs:137-188`;
  eager connector build in `SpiceClient::new`:
  `src/client.rs:123-137`; TLS wrap + `ServerName`:
  `src/client.rs:223-231`.
- Config struct: `src/lib.rs:80-94`.
- Reference semantics:
  `/srv/src-reference/spice/spice-common/common/ssl_verify.c`
  — parser `:282-372`, comparison `:374-425`. Providing
  the subject enables the check and replaces hostname
  verification (`spice-gtk/src/spice-session.c:788-795`).
- Fuzz manifest pattern: `fuzz/Cargo.toml` (detached
  `[workspace]`, three existing `[[bin]]` blocks); CI
  matrix `.github/workflows/ci.yml:72-90`.
- Test precedent: `link.rs:827+` (`mod tests`, duplex
  streams); no tests in `client.rs` today.
- rcgen 0.14 API: build a CA via
  `CertificateParams`/`KeyPair` and sign a leaf whose
  `distinguished_name` pushes `DnType` entries in a
  chosen order; `cert.der()` yields the DER to feed the
  verifier. Ryll's `--web` TLS tests and kerbside's
  `load_acceptor` tests are in-tree usage examples.
- Docker-wrapped gates: `make lint`, `make test` at the
  ryll repo root.

## Execution

One commit per step, in order:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Create `shakenfist-spice-protocol/src/host_subject.rs` implementing design decisions 1-3 (parser + `ExpectedSubject` + `matches_cert_der`), with `pub mod host_subject;` wired into `lib.rs` following the crate's module style. Derive semantics from `ssl_verify.c` (paths above), not from memory. Include exhaustive unit tests in the same file: parse round-trips (simple, multi-entry, escaped comma, escaped backslash, leading spaces before keys, trailing comma), parse errors (bad escape, empty value, missing `=`, unknown key, empty key mid-string), and matcher tests against hand-built DER via `rcgen` dev-dep (exact match; value case difference matches; internal whitespace run matches; wrong value, extra AVA, missing AVA, reordered AVAs all mismatch; emailAddress and DC attributes; undecodable value type mismatches if constructible). Add `x509-parser = "0.18"` and dev-dep `rcgen = "0.14"` to the crate manifest. `make lint && make test` must pass. |
| 1b | high | opus | none | Wire enforcement into `src/client.rs` per design decision 4: `expected_subject` field on `SpiceCaVerifier`, eager parse in `SpiceClient::new` (construction error on malformed pin), verifier installed when custom CA OR pinned subject, subject check in both accept paths of `verify_server_cert` with the specified `tracing::warn!` and `NotValidForName` error. Update the `lib.rs` doc comments (decision 7). The commit message must state the behaviour change for ryll users. `make lint && make test` must pass. |
| 1c | medium | sonnet | none | Add verifier-level integration tests in a new `#[cfg(test)]` module in `client.rs`: build a `SpiceCaVerifier` directly from an rcgen CA root store plus `expected_subject`, and assert `verify_server_cert` accepts a matching leaf, rejects a mismatching leaf with `InvalidCertificate(NotValidForName)`, still accepts a hostname-mismatched-but-subject-matching leaf, and preserves today's relaxed behaviour when `expected_subject` is `None`. Also assert `SpiceClient::new` fails on a malformed `host_subject` and succeeds with `None`. Follow the test-support patterns step 1a established. `make lint && make test` must pass. |
| 1d | medium | sonnet | none | Add the `fuzz_parse_host_subject` cargo-fuzz target per design decision 6: new file in `fuzz/fuzz_targets/` calling `parse_host_subject` on `String::from_utf8_lossy(data)`, a `[[bin]]` block in `fuzz/Cargo.toml` matching the existing three, and the target name appended to the matrix list at `.github/workflows/ci.yml:81`. Verify the target compiles the same way CI does (nightly `cargo fuzz build fuzz_parse_host_subject`, or at minimum `cargo check` inside `fuzz/` if nightly is unavailable locally — say which you did). |
| 1e | low | sonnet | none | Documentation sweep per design decision 7: ryll `docs/configuration.md` (`host-subject` row at :144 and the example at :187 — now enforced, connection fails on mismatch, malformed values error at startup), ryll `README.md` (the `.vv` key list around :196), and the crate `README.md` if it describes TLS verification behaviour. Keep edits factual and small; match surrounding prose style. |

After each step the management session reviews per the
master plan checklist, additionally cross-checking 1a's
semantics line by line against `ssl_verify.c` and
running `cargo deny check` after 1a introduces the new
dependency.

## Success criteria

* `host_subject: None` — byte-for-byte identical
  behaviour to today (existing tests unchanged and
  passing).
* Malformed expected-subject strings fail
  `SpiceClient::new`; they can never silently disable
  pinning.
* A pinned subject accepts exactly the certificates
  spice-gtk would accept for the same string (semantics
  derived from and tested against `ssl_verify.c`
  behaviour, including escaping, ordering, count, and
  case/whitespace folding).
* Mismatches fail the handshake with
  `InvalidCertificate(NotValidForName)` and a warn-level
  log naming both subjects.
* The new parser has a fuzz target built by CI; `make
  lint` and `make test` pass; `cargo deny check` is
  clean.
* Ryll's user-facing docs describe the enforcement and
  the behaviour change is recorded for release notes.

## Future work (recorded, not in this phase)

* Kerbside adoption (pin bump, CI proof, docs) — phase 2.
* Friendlier GUI error surfacing in ryll for subject
  verification failures.
* Unicode case folding in value comparison, should a
  real deployment ever need it.
* A long-running fuzz campaign is tracked separately in
  shakenfist/ryll#135.

## Outcome

Implemented 2026-07-17 on ryll branch `host-subject-verifier`
(five commits, one per step):

- `aeca24a` (1a) — `src/host_subject.rs`: parser + matcher +
  23 unit tests; x509-parser/rcgen added with zero new
  lockfile packages. Reviewed line-by-line against
  `ssl_verify.c`; every accept/reject agrees, with the one
  documented fail-closed divergence (multi-valued RDN order
  preserved rather than sorted).
- `5402dac` (1b) — enforcement in `SpiceCaVerifier`, both
  accept paths; custom verifier now also installed for
  pin-without-custom-CA. One deviation from the brief,
  adopted after review: the pin parses in
  `SpiceClient::new` unconditionally (not in the connector
  builder) so a malformed pin fails construction even when
  `tls_port` is None — the connector is only built for TLS
  configs, and the proxy's first leg is plaintext.
- `a5d07b0` (1c) — five verifier-level integration tests
  with rcgen CA/leaf certs (no SANs, like real SPICE
  certs), including the pin-substitutes-for-hostname case
  and the unpinned-relaxed regression guard.
- `c7e2c15` (1d) — `fuzz_parse_host_subject` target + CI
  matrix entry; validated locally via the devcontainer
  harness (build + 100,000-run smoke, no crashes).
- `b6cff33` (1e) — docs: `configuration.md` table row and
  example note, README parenthetical; crate README needed
  no change.

Open questions resolved during execution: x509-parser's
`as_str()` covers IA5String, so `emailAddress` needs no
special handling (BmpString correctly fails closed,
tested); spice-common's entry-count gate counts flattened
AVAs and the matcher flattens identically; the module is
exported as `pub mod host_subject` matching the crate's
existing style.

Gates: `make lint` and `make test` green across the
workspace (protocol crate at 133 tests); fuzz smoke green;
cargo-deny could not run on the bare host but the
dependency graph adds no new packages, and CI's
supply-chain job gates the PR.

Status: implementation complete; ryll push + PR pending
operator. Phase complete when merged to ryll develop.

## Back brief

Before executing any step of this plan, back brief the
operator on the intended approach and any deviations
from this plan discovered during implementation.
