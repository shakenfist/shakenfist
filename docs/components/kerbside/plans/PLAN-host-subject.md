# Backend host_subject enforcement

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the
Rust SPICE proxy in `rust/kerbside-proxy/` and the gRPC
control contract in `kerbside/rpc/`, the source driver
abstraction in `kerbside/sources/`, the SQLAlchemy/alembic
data model in `kerbside/db.py` and `alembic/`, audit
logging, and the .vv file generation path in
`kerbside/api.py`). Ground your answers in what the code
actually does today. Do not speculate about the codebase
when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

This plan crosses repository boundaries. Phase 1 lands in
`shakenfist/ryll` (the `shakenfist-spice-protocol` crate);
phase 2 lands in this repo. Sub-agents executing a
cross-repo phase must be briefed that the plan they are
following lives in `shakenfist/kerbside/docs/plans/` even
when all of their commits land elsewhere. Key references:

- `shakenfist/ryll` at `/home/tars/src/shakenfist/ryll` —
  the Rust SPICE client and home of
  `shakenfist-spice-protocol`. The verifier to change is
  `SpiceCaVerifier` in
  `shakenfist-spice-protocol/src/client.rs`.
- Reference checkouts at `/srv/src-reference/spice/` —
  the canonical subject-verification semantics are in
  `spice-common/common/ssl_verify.c`
  (`subject_to_x509_name`, `verify_subject`), wired up by
  `spice-gtk/src/spice-channel.c` and fed from `.vv`
  files by `virt-viewer/src/virt-viewer-session-spice.c`.
  Use these to verify matching semantics rather than
  guessing.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension. Tracking of
these sub-phases should be done via a table in this master
plan under the Execution section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The Python SPICE proxy verified the hypervisor's TLS
certificate subject against the console's `host_subject`
before relaying traffic. The Rust proxy that replaced it
(PLAN-rust-proxy, complete) does not: the ryll
`SpiceClient` it reuses validates the certificate chain
against the source's CA but deliberately relaxes
hostname/subject matching, because SPICE server
certificates commonly lack SAN extensions. This was
accepted as a security-parity regression for the phase-3
skeleton, recorded in PLAN-rust-proxy.md's future work as
"restore before production". This plan restores it.

Exploration findings that shape this plan (verified
2026-07-17 against the current trees):

- **The historical Python behaviour** (commit `bb68b555`,
  `kerbside/spiceprotocol/__init__.py:147-171`): optional
  — checked only when the console had a `host_subject` —
  and crude. It rebuilt a subject string from
  `ssl.getpeercert()`, mapping only `countryName`,
  `organizationName`, and `commonName` to `C`/`O`/`CN`
  (anything else became `UNKNOWN(...)`), joined with `,`,
  and did a single exact, case- and order-sensitive
  string compare. On mismatch it raised
  `HostSubjectInvalid`, refusing the hypervisor
  connection with an audit event. There were no unit
  tests for it.
- **The plumbing already exists end to end and is
  inert.** The proto carries it
  (`kerbside/rpc/kerbside.proto:100`, `Target
  .host_subject` field 7); the servicer fills it from the
  console (`kerbside/rpc/servicer.py:133`, empty string
  when NULL); the proxy maps empty→`None` and passes it
  into ryll's `ConnectionConfig.host_subject`
  (`rust/kerbside-proxy/src/backend.rs:197-207`, with a
  `TODO(host_subject)` comment); and ryll's config field
  is documented "informational only — subject matching is
  not enforced" (`shakenfist-spice-protocol/src/lib.rs:93`).
  Ryll's own `.vv`/INI parsing also already reads
  `host-subject` into its config
  (`ryll/src/config.rs:217,243,369`). No public API
  signature needs to change anywhere.
- **The enforcement gap is one function.**
  `SpiceCaVerifier::verify_server_cert`
  (`shakenfist-spice-protocol/src/client.rs:47-78`)
  delegates chain/expiry/signature checks to rustls's
  `WebPkiServerVerifier`, then swallows exactly the
  `NotValidForName` error class. It never inspects the
  subject. The custom verifier is only installed when a
  custom CA is configured (`client.rs:161-185`); without
  one, stock rustls verification (including hostname
  checks) applies.
- **Canonical matching semantics** live in spice-common's
  `ssl_verify.c`, shared by spice-gtk and fed verbatim
  from `.vv` `host-subject` fields by virt-viewer:
  - Parse (`subject_to_x509_name`, `ssl_verify.c:282-372`):
    comma-separated `key=value` entries; backslash may
    escape only `\` and `,` (anything else is a hard
    error); leading spaces before a key are skipped;
    an empty value is an error; keys are OpenSSL short
    names (`CN`, `O`, `C`, ...); entry order is
    preserved.
  - Compare (`verify_subject`, `ssl_verify.c:374-425`):
    the peer certificate's subject must have exactly the
    same number of RDN entries, then `X509_NAME_cmp`
    decides — order-sensitive, per-RDN, with OpenSSL's
    canonical form (case-insensitive, whitespace-folded
    values for directory-string types).
  - Providing the subject string is what switches
    verification on (`spice-session.c:788-795` sets
    `SPICE_SESSION_VERIFY_SUBJECT`); subject verification
    replaces hostname verification, it does not add to
    it.
- **Who supplies real values:** only the oVirt source
  scraper populates `host_subject` in production, from
  the oVirt API's `host.certificate.subject`
  (`kerbside/sources/ovirt.py:99-116`). The static
  source accepts it as an optional YAML key; Shaken Fist
  always sends `None`. The DB column is nullable
  (`kerbside/db.py:179`).
- **There is no test coverage anywhere.** The direct-qemu
  CI lane runs the backend qemu leg in plaintext — no
  hypervisor TLS certificate exists in any harness
  (`tools/direct-qemu/generate-tls.sh` generates only the
  proxy's client-facing certs). The Rust unit tests only
  cover the empty-string→`None` config mapping
  (`backend.rs:356-369`). Ryll's crate has no TLS
  verifier tests at all (`client.rs` has no test module),
  though it has good precedent: in-memory duplex-stream
  handshake tests in `link.rs`, cargo-fuzz targets under
  `shakenfist-spice-protocol/fuzz/`, and `rcgen` as the
  ecosystem's dev-dependency for minting test certs
  (already used by ryll's --web tests and kerbside's
  `load_acceptor` tests).
- **A documentation bug:** `docs/proxy-architecture.md:57-60`
  already claims the backend leg "verifies the server
  against the expected `host_subject`". That is not true
  today; this plan makes it true.
- **Crate release mechanics:** kerbside consumes
  `shakenfist-spice-protocol` as a git dependency pinned
  to a ryll commit (`rust/kerbside-proxy/Cargo.toml`,
  `rev = "1c6f19f3..."`). There is no crates.io publish
  loop; "release" means landing on ryll develop and
  bumping the `rev` pin in kerbside.
- **A naming collision to keep clear of:** the proxy's
  `--host_subject` CLI flag
  (`rust/kerbside-proxy/src/main.rs:101-102`) and
  `config.PROXY_HOST_SUBJECT` are the *proxy's own*
  client-facing certificate subject, placed into `.vv`
  files for the client→proxy leg. This plan is about the
  proxy→hypervisor leg only.

## Mission and problem statement

Restore hypervisor certificate subject pinning to the
proxy's backend leg, at security parity with (and better
fidelity than) the deleted Python implementation: when a
console carries a `host_subject`, the proxy must refuse to
relay to any backend whose end-entity certificate subject
does not match it, and the refusal must be observable in
audit events. When no `host_subject` is set, behaviour is
unchanged. The enforcement lands in the ryll crate's
verifier so that ryll (whose `.vv` parsing already carries
`host-subject` values it silently ignores) gains the same
protection for free.

### Design decisions (settled)

1. **Matching semantics follow spice-common, not the old
   Python code.** The expected-subject string is parsed
   and compared per `ssl_verify.c`: comma-separated
   `key=value` with backslash-escaping of `\` and `,`
   only, leading-space skip before keys, empty values
   rejected; comparison requires the same RDN count, the
   same attribute types in the same order, and
   case-insensitive, whitespace-folded value comparison.
   Rationale: interoperability — the same strings
   operators already use in `.vv` files and that oVirt's
   API supplies then behave identically in virt-viewer,
   ryll, and the kerbside proxy. The Python
   implementation was an approximation that broke on any
   RDN outside C/O/CN.
2. **Enforcement lives in ryll's `SpiceCaVerifier`,
   keyed off `ConnectionConfig.host_subject`.** `None`
   preserves today's behaviour exactly. `Some(subject)`
   enables the check: after chain verification, parse the
   end-entity certificate's subject DN and compare. As in
   spice-gtk, subject verification substitutes for
   hostname verification (the existing `NotValidForName`
   relaxation stays). The verifier must also be installed
   when `host_subject` is set *without* a custom CA —
   today the custom verifier only exists in the
   custom-CA path.
3. **Fail closed.** A malformed expected-subject string,
   an unparseable end-entity certificate subject, or a
   mismatch all fail the TLS handshake (surfaced as
   `CertificateError::NotValidForName` with a descriptive
   log line). A security control that silently skips on
   bad input is worse than none.
4. **No new heavyweight dependencies.** Subject DN
   parsing uses `x509-parser` 0.18, which is already in
   the ryll workspace lockfile (via rcgen 0.14) — it
   becomes a direct dependency of the protocol crate.
   Tests mint certificates with `rcgen` 0.14 as a
   dev-dependency, matching ryll and kerbside precedent.
   The expected-subject parser gets a cargo-fuzz target
   alongside the existing three.
5. **Enforcing from day one, in both consumers.** No
   config gate and no log-only grace period: there are no
   production deployments to disrupt, matching the
   firewall precedent from PLAN-rust-proxy. For ryll this
   is a deliberate behaviour change — `.vv` files with a
   stale `host-subject` will now fail — and is called out
   in its changelog.
6. **End-to-end proof in CI.** The direct-qemu lane gains
   a TLS-enabled backend: qemu's SPICE server presents a
   certificate with a known subject, the mock gRPC server
   supplies matching and deliberately mismatching
   `host_subject` values, and the lane asserts both the
   happy path (session relays) and the refusal path
   (connection refused, audit event emitted).

## Open questions

- How deep to take canonicalisation parity with
  `X509_NAME_cmp`: proposed is ASCII case-insensitive
  comparison with internal whitespace collapsed, applied
  to UTF8String/PrintableString values. Full
  nameprep-style canonicalisation is out of scope.
- Which attribute short names the parser accepts:
  proposed is the set spice deployments actually use
  (`CN`, `O`, `OU`, `C`, `L`, `ST`, `emailAddress`), with
  unknown keys a parse error (fail closed). To be
  finalised in phase-1 planning against x509-parser's OID
  registry.
- Whether a subject mismatch should emit a distinct audit
  event type, or reuse the generic hypervisor-connection-
  failed event with a distinguishing message (the Python
  proxy did the latter). Lean: reuse with a clear
  message; decide in phase-2 planning.
- Whether the oVirt-supplied `host.certificate.subject`
  string format (separator, spacing) matches the
  spice-common parse rules exactly, or needs
  normalisation at scrape time. ANSWERED in phase 2:
  the live value is `O=local,CN=ovirt.local`, which
  parses cleanly — no normalisation needed.
- Whether ryll should surface subject-verification
  failures to its GUI users with a friendlier error than
  a TLS handshake failure. Ryll-side polish, not
  blocking.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Subject pinning in shakenfist-spice-protocol (ryll repo) | PLAN-host-subject-phase-01-ryll-verifier.md | Complete (merged to ryll develop, ryll PR #166) |
| 2. Kerbside adoption: pin bump, CI proof, docs | PLAN-host-subject-phase-02-kerbside-adoption.md | Complete (kerbside PR #114; CI proof green, oVirt format verified) |

Phase sketches (each to be expanded into its own plan file
before execution):

1. **Subject pinning in the crate (ryll repo).** Factor a
   `host_subject` module in `shakenfist-spice-protocol`:
   parse the expected-subject string (spice-common
   semantics) and compare against an end-entity
   certificate's subject DN via `x509-parser`. Teach
   `SpiceCaVerifier` to run the comparison after chain
   verification when `ConnectionConfig.host_subject` is
   set, and install the custom verifier in the
   no-custom-CA case too when a subject is pinned. Update
   the `lib.rs` doc comments that currently promise
   non-enforcement. Unit tests with rcgen-minted certs
   (match; value case difference — accept; RDN order
   difference — reject; missing/extra RDN — reject;
   escaped comma in a value; malformed expected string —
   reject); a `fuzz_parse_host_subject` fuzz target; a
   changelog note for the ryll behaviour change. Land on
   ryll develop.
2. **Kerbside adoption.** Bump the `rev` pin in
   `rust/kerbside-proxy/Cargo.toml` to the ryll commit;
   delete the `TODO(host_subject)` comment in
   `backend.rs` and extend its unit tests; verify the
   audit path surfaces refusals (parity with the Python
   proxy's "Hypervisor connection failed"); extend the
   direct-qemu CI lane with a TLS-enabled qemu SPICE
   backend and positive/negative host_subject assertions;
   correct `docs/proxy-architecture.md` and
   `docs/console-sources.md`; check the oVirt subject
   string format question above.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort
   level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or the
   model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This applies to all steps, including high-effort ones. If
a sub-agent can't succeed even with a detailed brief and
the right model, that's a signal the brief needs
improving, not that the management session should do the
implementation itself.

Use `isolation: "worktree"` for sub-agents when the change
is risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood
changes, sub-agents can work directly in the main tree.

### Planning effort

The master plan itself should always be created at **high
effort** — it requires broad codebase understanding,
cross-referencing multiple source files, and making
judgment calls about scope and sequencing.

Phase 1 should be planned at **high effort**: it encodes
wire-adjacent security semantics (DN parsing and
comparison rules cross-checked against spice-common) where
a subtle divergence silently weakens or breaks the
control. Phase 2 can be planned at **medium effort** for
the pin bump and docs, but the CI fixture work (TLS-enabled
qemu SPICE backend) should be treated as high-effort
within that plan.

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 1a   | medium | sonnet | none      | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree  | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making
  judgment calls, understanding non-obvious invariants
  (X.509 DN encoding, rustls verifier contracts, TLS
  handshake failure surfaces, audit log guarantees), or
  researching external references. The sub-agent needs to
  think carefully about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a
  few files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat,
  add a log line). The brief is a complete instruction.

**Model choice:** The planner should recommend which
model is best suited for each step. This is a judgment
call, not a rigid rule — the right model depends on what
the step requires, not on whether it's "planning" or
"implementation".

- **opus** — Best for steps that require deep reasoning,
  cross-file architectural understanding, subtle
  correctness judgment, security-sensitive parsing and
  comparison logic, or intricate implementation where
  getting it wrong would be costly to debug (a subject
  matcher that accepts too much is an invisible security
  hole; one that accepts too little is a production
  outage).
- **sonnet** — Good default for well-briefed
  implementation work. Faster and cheaper than opus.
  Works well when the plan front-loads the research and
  the brief is detailed enough that the agent doesn't
  need to make broad judgment calls.
- **haiku** — Suitable for purely mechanical tasks:
  search-and-replace, adding log lines, running
  commands. The brief must be a near-complete
  instruction.

The model choice interacts with effort level and brief
quality. A detailed brief compensates for a lighter model
— sonnet at medium effort with a thorough brief often
matches opus at medium effort with a vague brief. The
planner's job is to write briefs good enough that the
recommended model can succeed.

Note: the model also determines the context window (opus
has 1M tokens, sonnet and haiku have 200K). Steps that
require holding many files in context simultaneously may
need opus for that reason alone, even if the reasoning
itself is straightforward.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable. A
failed or low-quality implementation wastes more time
(and therefore more money) than using a heavier model
would have cost. Only recommend a lighter model when you
are confident the brief is detailed enough for it to
succeed.

**Brief for sub-agent:** This is the key field. Write it
as if briefing a colleague who has never seen the
codebase. Include: what to change, which files to touch,
what patterns to follow, and any non-obvious constraints.
The better the brief, the lower the effort level needed
and the lighter the model that can succeed.

A good brief front-loads the research the planner
already did, so the implementing agent doesn't repeat it.
For example, instead of "make the verifier check the
subject", write "in
`shakenfist-spice-protocol/src/client.rs`, extend
`SpiceCaVerifier` (struct at line ~33, `verify_server_cert`
at ~47) with an `expected_subject: Option<ParsedSubject>`
field populated from `ConnectionConfig.host_subject`
(`lib.rs:93`) in `create_tls_connector` (~137); after the
existing webpki match, parse `end_entity` with
`x509_parser::X509Certificate::from_der` and compare per
the rules in the plan's design decision 1, returning
`Error::InvalidCertificate(CertificateError::NotValidForName)`
on mismatch."

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] Python changes pass `tox -eflake8` and `tox -epy3`.
- [ ] Rust changes pass `cargo fmt --check`,
      `cargo clippy`, and `cargo test` in the affected
      workspace; new parsers have fuzz targets and the
      fuzz targets at least build.
- [ ] Matching semantics are cross-checked against
      `/srv/src-reference/spice/spice-common/common/ssl_verify.c`
      — not against memory of it.
- [ ] The changes match the intent of the brief — not
      just syntactically correct but semantically right.
- [ ] Commit message follows project conventions
      (including the `Co-Authored-By` line with model,
      context window, effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* A console with a `host_subject` set relays only to a
  backend presenting a certificate whose subject matches
  under the spice-common rules; a mismatched, extra-RDN,
  reordered, or malformed subject refuses the connection
  and the refusal is visible in audit events.
* A console without a `host_subject` behaves exactly as
  today.
* The same subject string accepted or rejected by the
  proxy is accepted or rejected identically by
  virt-viewer/spice-gtk against the same certificate
  (semantics parity, demonstrated by tests derived from
  `ssl_verify.c`, not necessarily by running spice-gtk).
* Ryll headless connecting with a `.vv` containing
  `host-subject` enforces it.
* The direct-qemu CI lane exercises a TLS backend with
  both matching and mismatching subjects on every PR.
* The expected-subject parser has a cargo-fuzz target;
  the Rust code passes `cargo fmt --check`, `cargo
  clippy` without warnings, and `cargo test` in both
  repos; any Python touched passes `tox -eflake8` and
  `tox -epy3`.
* `rust/kerbside-proxy/Cargo.toml` pins a ryll `rev`
  containing the enforcement, and the
  `TODO(host_subject)` comment in `backend.rs` is gone.
* `docs/proxy-architecture.md` and
  `docs/console-sources.md` accurately describe the
  behaviour (the former currently over-claims), and
  ryll's changelog records the behaviour change.
* PLAN-rust-proxy.md's future-work entry for backend
  `host_subject` enforcement is marked done with a
  pointer to this plan.

### Future work

* Per-source or per-console policy on *requiring* a
  host_subject (today a NULL subject silently disables
  the check; a paranoid deployment may want
  "no subject, no connection").
* Surfacing subject-verification failures in ryll's GUI
  with a friendlier error.
* An OpenStack source scraper does not exist today; if
  one is added, wire subject scraping into it from the
  start.
* Consider crates.io publication of
  `shakenfist-spice-protocol` instead of git-rev pinning
  (deferred from PLAN-rust-proxy).

### Bugs fixed during this work

None yet. When planning phase 1, scan the
shakenfist/kerbside and shakenfist/ryll issue trackers
for open bugs related to backend TLS, certificate
handling, or host_subject, and either fold them into the
relevant phase or record them here as consciously
deferred. Known already: `docs/proxy-architecture.md:57-60`
documents behaviour that does not exist (fixed by phase
2).

### Documentation index maintenance

When creating a new master plan from this template,
update the following file in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological
  order.

When all phases of a plan are complete, update the
status column in `index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
