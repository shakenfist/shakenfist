# Phase 8: Native TLS + operator docs + systemd

## Prompt

Before responding to questions or making changes, read the
master plan at `docs/plans/PLAN-web-frontend.md` (Phase 8
section in the Execution table and prose), and the current
state of the operator-facing docs:

- `docs/web-frontend.md` — already covers Quick start, What
  works, Reconnect behaviour, Limitations, Security note,
  CI smoke test, Pending phases. Phase 8 fills the gaps.
- `README.md` — current `--web` description includes a
  pointer to `docs/web-frontend.md` and notes that
  packaging / docs are 0–7/8 complete with Phase 8 listed
  as pending.
- `ARCHITECTURE.md` — Phase 6/7 paragraphs already exist;
  Phase 8 may add a small operator-facing pointer.
- `AGENTS.md` — `--web` is in the modes list.
- `docs/portability.md` — `--web` is verified Linux-only.

Cross-reference: the master plan calls out
`kerbside/docs/` as a place to consider adding a brief
mention of ryll's `--web` mode as a deployment pattern.
Kerbside is a separate repo at
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/kerbside/`.

External references (no need to read upfront, but useful
during implementation): systemd unit syntax (man
`systemd.unit`, `systemd.service`); Caddy automatic-HTTPS
docs; nginx `proxy_pass` patterns; Let's Encrypt /
certbot; mkcert for local dev; `Type=simple` vs
`Type=notify` for graceful shutdown.

Flag any uncertainty rather than guessing.

## Goal

Make the signalling page TLS-capable in the binary itself,
then close the operator-facing documentation gap so a
sysadmin who does not know the codebase can:

- Serve `ryll --web` directly over HTTPS with a cert pair
  (no required reverse proxy).
- Run it as a long-lived systemd service.
- Diagnose common failure modes (no video, ICE failure,
  no audio, autoplay blocked) without reading source.
- Know where ryll's `--web` mode fits in the broader
  shakenfist deployment story (the kerbside cross-ref).

The motivation for native TLS over a documented reverse-
proxy pattern: SPICE's existing UX flags unencrypted
sessions explicitly via the notification system. Shipping a
web frontend that *requires* an external proxy for HTTPS
would mislead operators by quietly serving the page (and
the per-launch URL token) in cleartext on the wire whenever
they're not careful. Native TLS keeps the security story
"what ryll prints is what's used" without an extra moving
part. The reverse-proxy path is documented as an option
for operators who already run one, but it's no longer the
recommended baseline.

After Phase 8:

- `ryll --web --web-tls-cert /path/cert.pem
  --web-tls-key /path/key.pem` serves the signalling page
  over HTTPS (and prints `https://...` in the URL line).
  Without those flags, behaviour is unchanged
  (plain-HTTP, loopback default).
- `docs/web-frontend.md` has Service mode, Native TLS,
  Cert recipes, Troubleshooting, and Deployment patterns
  sections.
- A reference systemd unit lives at `examples/ryll-web.service`
  showing the TLS-mode invocation.
- An optional reference Caddyfile lives in the docs as a
  fallback recipe for operators who want auto-fetched
  certs without managing them in ryll's config; this is
  no longer the primary recommendation.
- `kerbside/docs/` has a one-paragraph note pointing at
  ryll's `--web` mode.
- The web-frontend project status flips to Complete.

Out of scope:

- ACME / auto-cert support inside ryll. Operators bring
  their own cert pair (mkcert, certbot, internal CA, etc.).
  ACME is a substantial dependency surface and a future
  enhancement.
- Multiple cert support / SNI. One cert pair per ryll
  instance.
- mTLS (client certs for browser auth). The per-launch
  URL token remains the auth boundary.
- Multi-viewer support, OIDC/SAML, audit logging, rate
  limiting — future work.
- A Docker / Podman container image for ryll-as-a-service.
- Distro-specific packaging tweaks beyond what Phase 7
  already shipped.

## Scope

In:

- **Native TLS in axum** — `ryll/Cargo.toml`,
  `ryll/src/main.rs`, `ryll/src/web/server.rs`,
  `ryll/src/web/mod.rs`. Add `axum-server = { version,
  features = ["tls-rustls"] }` (it already pins
  rustls 0.23 with ring, matching the workspace).
  CLI flags `--web-tls-cert <PATH>` and
  `--web-tls-key <PATH>` (both must be supplied or
  neither). When present, bind the server with
  `axum_server::bind_rustls(addr, RustlsConfig)`.
  Otherwise, behaviour stays plain-HTTP via existing
  `axum::serve(...).with_graceful_shutdown(...)` path.
  Print `https://...` in the URL line when TLS is on.
- **Graceful shutdown parity** — axum-server uses a
  `Handle` for shutdown, not the `with_graceful_shutdown`
  pattern. Wire `Handle::graceful_shutdown(Some(timeout))`
  to fire when SHUTDOWN_REQUESTED flips, mirroring the
  Phase 6 fix. Phase 7c's `tools/web-smoke.sh` SIGTERM
  test must still pass for both the plain-HTTP and TLS
  paths.
- **Tests** — extend `ryll/src/web/server.rs` (or a new
  `tls.rs` integration test) to cover: flag parsing
  (both / neither / one-of-pair-missing); cert loading
  errors are surfaced clearly; a self-signed cert
  generated via `rcgen` (dev-dep) binds and serves the
  embedded HTML over HTTPS.
- **Smoke test extension** — `tools/web-smoke.sh` gains
  a TLS variant or grows a `--tls` flag. CI's Linux
  step exercises both. Generates a throwaway cert via
  openssl in the script's tempdir.
- `docs/web-frontend.md` — extend with:
  - **Service mode** section: systemd unit, user/group,
    `EnvironmentFile` for `.vv` and cert paths,
    `Restart=on-failure`, `KillSignal=SIGTERM` ties
    Phase 6 shutdown, journalctl URL extraction.
  - **Native TLS** section: how to invoke
    `--web-tls-cert/--web-tls-key`, how to obtain a
    cert (mkcert, certbot, internal CA recipe), how
    to rotate a cert (kill + restart; no inline
    reload in MVP).
  - **Cert recipes**: mkcert for LAN dev; certbot
    standalone for public DNS; openssl for one-off
    self-signed; pointer to internal CA workflow.
  - **Reverse proxy fallback**: brief Caddy two-line
    recipe + warning callout that WebRTC media is
    NOT proxied (UDP RTP must reach ryll's host
    directly regardless).
  - **Troubleshooting**: no video; ICE failure (UDP
    blocked); no audio (autoplay policy / PCM-only
    server); "Click to reconnect" loops; cert-load
    errors; Ctrl-C historic note (Phase 6).
  - **Deployment patterns** appendix: localhost-only;
    LAN-only with mkcert; public-DNS with certbot;
    behind a corporate proxy (the fallback).
- `examples/ryll-web.service` — reference systemd unit
  (TLS-enabled invocation).
- `kerbside/docs/<file>.md` — one-paragraph note pointing
  at ryll `--web` for browser access (separate repo;
  separate commit).
- `README.md` — flip the multi-modal table from "0–7 of
  8" to Complete; flip the `--web` row to Shipping with
  a "TLS native" qualifier in the description so the
  README reflects the security posture.
- `docs/plans/PLAN-web-frontend.md` — Phase 8 row
  Complete; project status Complete.
- `docs/plans/index.md` — web-frontend project Complete.

Out:

- All items in "Out of scope" above.
- Reorganising the existing `docs/web-frontend.md` —
  append, don't refactor.
- A Container/Docker recipe (deferred).
- A reverse-proxy "primary" path. The Caddy recipe ships
  as a fallback only and that framing matters.

## Approach

### Native TLS (8a)

Use `axum-server` (the maintained companion crate to axum
that exposes a binding loop with TLS support). It already
talks to rustls 0.23 via the `tls-rustls` feature, which
matches the workspace's existing rustls + ring pinning.
No extra crypto provider work needed.

API sketch:

```rust
use axum_server::{tls_rustls::RustlsConfig, Handle};

let handle = Handle::new();
// Wire SHUTDOWN_REQUESTED → handle.graceful_shutdown(...).
let shutdown_handle = handle.clone();
tokio::spawn(async move {
    while !SHUTDOWN_REQUESTED.load(Ordering::Relaxed) {
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    shutdown_handle.graceful_shutdown(Some(Duration::from_secs(5)));
});

if let (Some(cert), Some(key)) = (&args.web_tls_cert, &args.web_tls_key) {
    let config = RustlsConfig::from_pem_file(cert, key).await
        .with_context(|| format!("loading TLS cert/key from {} / {}", cert.display(), key.display()))?;
    axum_server::bind_rustls(addr, config)
        .handle(handle)
        .serve(app.into_make_service())
        .await?;
} else {
    axum_server::bind(addr)
        .handle(handle)
        .serve(app.into_make_service())
        .await?;
}
```

Both branches use `axum_server` (not the existing
`axum::serve`) so the shutdown semantics are uniform.
This is a small migration; the existing
`with_graceful_shutdown(shutdown_signal())` pattern goes
away in favour of `Handle::graceful_shutdown`.

CLI flag pair, mutually-required:

```rust
/// PEM-encoded TLS certificate chain. If supplied, --web-tls-key
/// is also required and the web frontend serves over HTTPS.
#[arg(long, requires = "web_tls_key")]
pub web_tls_cert: Option<PathBuf>,

/// PEM-encoded TLS private key. Required if --web-tls-cert is
/// supplied.
#[arg(long, requires = "web_tls_cert")]
pub web_tls_key: Option<PathBuf>,
```

The `requires =` clap attribute enforces the both-or-
neither constraint at parse time.

URL printing — when TLS is on, the URL line becomes
`https://host:port/?token=...` instead of `http://...`.
Affects the operator-visible startup log line and the
journalctl extraction recipe in the systemd doc.

Test surface:

1. Unit test for flag parsing (both / neither / one-of-
   pair-missing — clap's `requires` should reject the
   last case).
2. Cert-load error path (nonexistent file, malformed
   PEM) surfaces a clear `anyhow::Error` chain.
3. Integration test in `ryll/src/web/` that uses
   `rcgen` (dev-dep) to generate a self-signed cert,
   binds, and serves the embedded HTML over HTTPS via
   a reqwest client with cert verification disabled.

### Smoke test extension (still 8a, same commit)

`tools/web-smoke.sh` gains an optional second invocation
that:

1. Generates a throwaway cert pair via `openssl req
   -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem
   -days 1 -nodes -subj "/CN=localhost"` in the temp
   dir.
2. Launches `ryll --web --web-tls-cert ... --web-tls-key ...`.
3. curls `https://localhost:port/` with `-k` and asserts
   200 OK on the embedded HTML.
4. SIGTERMs and verifies clean exit (5 s ceiling, same
   as plain).

CI workflow runs the smoke script twice on the Linux
matrix entry: once plain, once TLS.

### Service mode (8b)

systemd unit at `examples/ryll-web.service`:

```ini
[Unit]
Description=ryll --web SPICE-to-browser transcoder
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ryll
Group=ryll
EnvironmentFile=/etc/ryll/web.env
# web.env declares:
#   VV_FILE=/etc/ryll/session.vv
#   WEB_HOST=0.0.0.0
#   WEB_PORT=8443
#   WEB_TLS_CERT=/etc/ryll/tls/cert.pem
#   WEB_TLS_KEY=/etc/ryll/tls/key.pem
ExecStart=/usr/bin/ryll --web \
    --file ${VV_FILE} \
    --web-host ${WEB_HOST} \
    --web-port ${WEB_PORT} \
    --web-tls-cert ${WEB_TLS_CERT} \
    --web-tls-key ${WEB_TLS_KEY}
Restart=on-failure
RestartSec=5s
KillSignal=SIGTERM
TimeoutStopSec=10s
StandardOutput=journal
StandardError=journal

# Hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadOnlyPaths=/etc/ryll
# ryll opens UDP for ICE; allow UDP egress.
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
```

The `web-frontend.md` "Service mode" section explains:
- Where to put the .vv file (`/etc/ryll/session.vv`,
  readable only by the ryll user).
- How to capture and rotate the per-launch URL/token —
  it's printed to stdout, so journalctl is the answer
  (`journalctl -u ryll-web -n 1 | grep -o 'http://.*'`).
- That `KillSignal=SIGTERM` is required (not the default
  for Type=simple is SIGTERM, but documenting explicitly
  protects against accidental override) so Phase 6's
  graceful-shutdown path engages.

### TLS recipes + reverse-proxy fallback (8c)

Caddyfile (the canonical recipe — autocert handles the
cert lifecycle):

```caddy
ryll.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

That's the entire config for a publicly-reachable
deployment with an A record pointing at the host. Caddy
talks to Let's Encrypt automatically.

For LAN-only or self-signed:

```caddy
ryll.lan:8443 {
    tls /etc/ssl/ryll.crt /etc/ssl/ryll.key
    reverse_proxy 127.0.0.1:8080
}
```

nginx equivalent (for sysadmins who already run nginx):

```nginx
server {
    listen 443 ssl http2;
    server_name ryll.example.com;

    ssl_certificate     /etc/letsencrypt/live/ryll.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ryll.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        # No upgrade/Connection headers needed: WebRTC negotiates
        # out-of-band over UDP after the initial /offer POST.
        proxy_buffering off;
    }
}
```

**Important note** for both: the WebRTC media path is
NOT proxied. ICE candidates from ryll point at ryll's
host and port directly; the browser opens a UDP flow
to that endpoint. The reverse proxy carries only the
HTTP signalling page + `/offer` POST. This means:

- ryll's UDP port range must be reachable from the
  browser (firewall holes for the ephemeral RTP ports).
- Bind ryll's `--web-host` to the public-facing IP, not
  to 127.0.0.1, when behind a proxy that itself listens
  on a different IP.

This caveat is the section's most important takeaway
and goes in a callout box in `docs/web-frontend.md`.

### Cert recipes (8c cont.)

- **Let's Encrypt via Caddy**: nothing to do; Caddy
  fetches and renews. Document the prerequisite (DNS A
  record, port 80/443 reachable).
- **Let's Encrypt via certbot + nginx**:
  `certbot --nginx -d ryll.example.com`. Assumes nginx
  is already serving the domain on port 80.
- **mkcert for LAN dev**:
  ```
  mkcert -install
  mkcert ryll.lan 192.168.1.10
  ```
  Drop the resulting `.pem` files into the Caddyfile or
  nginx config. Trust is on the dev's machine via
  mkcert's local CA.
- **Self-signed for one-off use**:
  ```
  openssl req -x509 -newkey rsa:2048 -keyout key.pem \
    -out cert.pem -days 30 -nodes -subj "/CN=ryll.lan"
  ```
  Browser will show a warning — acceptable for one-off
  diagnostic access.

### Troubleshooting (8c cont.)

Section structure: symptom → likely cause → fix.

- **Page loads, video stays black for >10 seconds**:
  - Check browser console for `RTCPeerConnection`
    state. If stuck on `connecting` → ICE failure (UDP
    blocked between browser and ryll). If on
    `connected` but no frames → encoder didn't start
    (check ryll's stderr).
  - Resolution: the encoder requests a keyframe on
    `Connected`; the very first frame can take up to
    1 second. If beyond that, encoder is wedged —
    file a bug.
- **No audio, video works**:
  - Browser autoplay policy: click the volume button
    on the page. The `<video>` is muted by default to
    satisfy the policy.
  - PCM-only SPICE server: ryll only does Opus
    passthrough in MVP. Server logs will say
    "playback channel negotiated PCM; web mode is
    silent until a future PCM→Opus encoder lands".
- **"Click to reconnect" loop**:
  - 5 attempts then manual button per Phase 6. If the
    server is alive but no offer is being accepted,
    check that the reaper is consuming the dead
    signal (logs: `bridge reaper: bridge died,
    reaping`). If the reaper is stuck, Ctrl-C and
    restart.
- **High CPU when no browser is connected**:
  - Phase 6 made the reaper proactive; if you see this
    on Phase 6+ ryll, the reaper isn't running or the
    bridge isn't reaching a terminal state. Check
    logs and file a bug.
- **Ctrl-C ignored**:
  - Pre-Phase-6 only — Phase 6's `with_graceful_shutdown`
    fixed this. Update to ryll ≥ Phase 6.

### kerbside cross-reference (8d)

Find the right home in `kerbside/docs/`. Likely
candidates: `index.md` (a "Related projects" or
"Console sources" pointer) or `proxy-architecture.md`
("ryll exposes the same SPICE channels but with a
browser frontend via WebRTC; useful when the operator
wants browser access without going through a separate
RDP / Guacamole stack"). Pick whichever fits the
existing structure best.

A single paragraph, ~3 sentences, with a link back to
`https://github.com/shakenfist/ryll` and the relevant
section of `docs/web-frontend.md`.

### Status flips (8e)

- `docs/plans/PLAN-web-frontend.md` — Phase 8 row
  Complete. Master plan project status: Complete.
- `docs/plans/index.md` — web-frontend row status:
  Complete.
- `README.md` — bump from "0–7/8" to "Complete";
  promote the `--web` mode from in-progress to
  shipping in the multi-modal table.
- `ARCHITECTURE.md` — short sentence in the multi-modal
  section noting that all eight phases shipped.

## Prerequisites

- Phase 7 complete on `thought-bubble`. (It is — last
  commit `5d14b053`.)
- User decision on native TLS confirmed: native TLS in.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a   | high   | opus   | worktree | Native TLS in axum. Add `axum-server = { version = "0.7", features = ["tls-rustls"] }` to `ryll/Cargo.toml`. Add `--web-tls-cert/--web-tls-key` flags (clap `requires =` enforces both-or-neither). Migrate the existing `axum::serve(...).with_graceful_shutdown(...)` path to `axum_server::bind(...)/bind_rustls(...).handle(handle).serve(...)` with a `Handle::graceful_shutdown` shim driven by `SHUTDOWN_REQUESTED`. Print `https://...` in the URL log line when TLS is on. Add `rcgen` as a dev-dep and write an integration test that loads a self-signed cert and serves the embedded HTML over HTTPS. Extend `tools/web-smoke.sh` (or add `tools/web-smoke-tls.sh`) and add a second CI step on Linux that runs the TLS variant. Single commit. |
| 8b   | medium | sonnet | none     | Service mode. Add `examples/ryll-web.service` (TLS-enabled per the plan). Add a "Service mode" section to `docs/web-frontend.md` covering EnvironmentFile pattern (including cert paths), journalctl URL extraction (now `https://`), how `KillSignal=SIGTERM` ties Phase 6's graceful shutdown. Document that cert rotation is "kill + restart" in MVP. Single commit. |
| 8c   | medium | sonnet | none     | Native TLS docs + cert recipes + reverse-proxy fallback + troubleshooting. Add **Native TLS**, **Cert recipes** (mkcert / certbot / openssl one-off), **Reverse-proxy fallback** (Caddy two-line, with WebRTC-not-proxied callout), and **Troubleshooting** sections to `docs/web-frontend.md`. Update the existing "Security note" to reflect that TLS is now a first-class option. Single commit. |
| 8d   | low    | sonnet | none     | kerbside cross-reference. Open `/srv/kasm_profiles/mikal/vscode/src/shakenfist/kerbside/docs/`, pick the right file (likely `index.md` or `proxy-architecture.md`), add a one-paragraph pointer to ryll `--web` mode noting native TLS. Commit in the kerbside repo (separate git context). Single commit. |
| 8e   | medium | sonnet | none     | Status flips + README + ARCHITECTURE polish. Flip Phase 8 + project status in master plan, index, README. Mention native TLS in the README's `--web` line. Add a brief paragraph to ARCHITECTURE.md noting the project landed end-to-end with native TLS. Single commit. |

After 8e, Phase 8 is done and the web-frontend project is
**Complete**.

## Step details

### Step 8a expanded brief

The single biggest moving part is the migration from
`axum::serve(...).with_graceful_shutdown(...)` to
`axum_server::bind(...).handle(handle).serve(...)`. The
existing path uses `with_graceful_shutdown(shutdown_signal())`
where `shutdown_signal()` polls SHUTDOWN_REQUESTED on a
small interval. The new path uses
`Handle::graceful_shutdown(Some(timeout))` from a spawned
task that polls SHUTDOWN_REQUESTED the same way.

Migration steps:

1. Add `axum-server = { version = "0.7", features = ["tls-rustls"] }`
   to `ryll/Cargo.toml`. Add `rcgen = "0.13"` (or current
   stable) to `[dev-dependencies]`.
2. In `ryll/src/web/server.rs` (or wherever the bind+serve
   lives — read `web::run` to confirm), replace the
   `axum::serve(...).await` with the axum-server bind
   pattern. Wire the Handle shutdown through the same
   SHUTDOWN_REQUESTED polling pattern that
   `with_graceful_shutdown` uses today.
3. In `ryll/src/main.rs`, accept the new `--web-tls-cert/
   --web-tls-key` args. Pass them through to `web::run`
   or build the `RustlsConfig` in main.rs and pass the
   prepared config (your call — wherever the existing
   bind code already lives).
4. The URL log line at `web::run` startup needs to know
   whether TLS is on. Pass a bool or read it from state.
5. The Phase 6 explicit-bridge-close + reaper-abort
   shutdown sequence stays unchanged; it executes after
   the server returns regardless of which bind variant
   was used.

Tests to add:

- `web_tls_flags_require_both` — clap rejects supplying
  only one of the pair.
- `web_tls_loads_self_signed_cert` — uses rcgen to
  generate a cert, calls `RustlsConfig::from_pem_file`
  with the resulting bytes, asserts no error.
- An integration test that binds a TLS server in a
  tokio test, hits `https://localhost:port/?token=...`
  with a reqwest client (`danger_accept_invalid_certs(true)`),
  and asserts 200 OK + the embedded HTML body.

For `tools/web-smoke.sh`, the cleanest extension is a
new flag `--tls`:

```bash
"$BIN" --web --web-port "$PORT" \
    --web-tls-cert "$cert" --web-tls-key "$key" \
    --file "$tmpvv" &
```

Use `curl -sk https://localhost:$PORT/` to verify the
HTTPS path serves the index. Generate the throwaway cert
inline:

```bash
openssl req -x509 -newkey rsa:2048 -keyout "$tmpdir/key.pem" \
    -out "$tmpdir/cert.pem" -days 1 -nodes \
    -subj "/CN=localhost" 2>/dev/null
```

CI: in `.github/workflows/ci.yml`, add a second
`Run --web smoke test (TLS)` step on the Linux matrix
entry. Both steps run on every PR.

### Step 8b expanded brief

The systemd unit needs:

- `Type=simple` (matches ryll's blocking-foreground main).
- `KillSignal=SIGTERM` (default but document explicitly).
- `TimeoutStopSec=10s` (Phase 6's shutdown drains within
  ~5s; 10s is generous).
- `Restart=on-failure` (auto-recover from crash; do NOT
  restart on success — operators should explicitly stop
  the service).
- Hardening: `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, `PrivateTmp`, `ReadOnlyPaths=/etc/ryll`,
  `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`.

EnvironmentFile pattern keeps the `.vv` path and
host/port out of the unit file so operators can edit
config without touching systemd. Document the example
`/etc/ryll/web.env`:

```
VV_FILE=/etc/ryll/session.vv
WEB_HOST=0.0.0.0
WEB_PORT=8080
```

Document journalctl recipe for token extraction:

```bash
journalctl -u ryll-web -n 50 --no-pager | grep -oE 'http://[^ ]+token=[^ ]+' | tail -1
```

### Step 8c expanded brief

The TLS section's most important content is the
WebRTC-media-not-proxied callout (still applies even
when the operator chooses the reverse-proxy fallback).
Make it prominent (blockquote / admonition / bold
call-out). Operators who don't read this will hit
"page loads, video black" because their proxy doesn't
forward the UDP RTP flow.

The Native TLS section is the primary recommendation
and goes first. Frame the reverse-proxy section as a
fallback for operators who already terminate TLS at a
proxy for unrelated reasons.

Caddy recipe is two lines because Caddy handles the
cert lifecycle automatically. nginx recipe is longer
because the operator needs to obtain the cert separately
(certbot or manual).

Cert recipes: keep each to ~5 lines. mkcert is the
LAN-dev pattern; certbot is the public-DNS pattern;
self-signed is the "I just need it to work for one
afternoon" pattern.

Troubleshooting: each entry follows the same shape:
**Symptom** in bold, then a short bulleted list of
likely causes with concrete logs to check. Don't write
essays.

### Step 8d expanded brief

Read `kerbside/docs/index.md` and
`kerbside/docs/proxy-architecture.md` to choose the
right file. The note should:

- Be ~3 sentences.
- Mention that ryll's `--web` mode is a browser
  frontend for SPICE that does not require kerbside,
  but kerbside operators may find it useful for
  internal access scenarios.
- Link to `https://github.com/shakenfist/ryll/blob/main/docs/web-frontend.md`.

Commit message in the kerbside repo follows kerbside's
conventions (check that repo's recent commit log).
Use the standard Co-Authored-By + Signed-off-by
trailers.

### Step 8e expanded brief

Mechanical doc updates:

- `docs/plans/PLAN-web-frontend.md`: Phase 8 row →
  Complete with the five 8a–8e commit SHAs. Master
  plan's overall project status (top of the file or
  wherever it lives) → Complete.
- `docs/plans/index.md`: web-frontend project status
  → Complete.
- `README.md`: flip the multi-modal table's web row to
  Shipping. Update progress to "All 8 phases complete"
  or similar.
- `ARCHITECTURE.md`: short sentence in the multi-modal
  section confirming `--web` mode shipped.

Don't write essays. The reconnect/CI prose from 6d/7e
is the right tone.

## Acceptance criteria

- `make lint` and `make test` pass after each step.
- After 8a: native TLS unit + integration tests pass
  (rcgen-based self-signed serving over HTTPS); both
  plain and TLS smoke-test variants run green in CI;
  flag-pair-required clap test passes; URL log line
  reports `https://` when TLS is on.
- After 8b: `examples/ryll-web.service` parses cleanly
  with `systemd-analyze verify` (or visual review
  against `man systemd.service`).
- After 8c: docs are coherent (Native TLS comes first,
  reverse-proxy is fallback, WebRTC callout prominent).
- After 8d: kerbside repo has a new commit referencing
  ryll `--web`.
- After 8e: master plan, index, README all flip to
  Complete.
- `pre-commit run --all-files` passes after each commit
  in the ryll repo.

## Risks

- **axum-server graceful-shutdown semantics differ.**
  axum-server's `Handle::graceful_shutdown(Some(timeout))`
  is one-shot and signals all in-flight connections to
  close. The existing `with_graceful_shutdown(future)`
  pattern is a future-driven trigger. Behaviour is
  equivalent but the wiring is different — verify the
  Phase 6 explicit-bridge-close sequence still runs
  after the bind future returns.
- **rustls feature drift.** axum-server's `tls-rustls`
  feature pulls a specific rustls version; verify it
  matches the workspace's existing rustls 0.23 + ring
  pinning. If it drifts, pin axum-server to a version
  that matches.
- **WebRTC-not-proxied caveat being missed.** Even with
  native TLS as the primary path, operators who choose
  the reverse-proxy fallback can still hit this. Keep
  the callout in the fallback section.
- **systemd unit hardening too aggressive.**
  `ProtectSystem=strict` + `ReadOnlyPaths=/etc/ryll`
  works for the common case but blocks an operator who
  wants to write logs to disk via `--log-file`. If the
  operator uses --log-file routinely, relax
  `ReadWritePaths` appropriately. Document the
  constraint.
- **Cert file permissions.** The systemd unit runs as
  user `ryll`; the key file must be readable by that
  user. Document the chmod recipe.
- **kerbside repo conventions.** This plan does NOT
  bundle the kerbside change with the ryll commits.
  Each repo gets its own commit; the kerbside change is
  pushed separately. Sub-agent for 8d works in the
  kerbside checkout.
- **Existing TLS reference in `docs/web-frontend.md`**
  ("wait for Phase 8's TLS support") needs editing to
  reflect that TLS is now native. 8c should rewrite
  that paragraph.
- **README "TLS support" line** (around line 26)
  references inline CA certs from `.vv` files — a
  separate feature unrelated to web-mode TLS. Don't
  conflate. The README's `--web` row is the place to
  call out native HTTPS.

## Documentation updates

After 8e:

- `docs/web-frontend.md` extended with Service mode,
  Native TLS, Cert recipes, Reverse-proxy fallback,
  Troubleshooting, Deployment patterns.
- `examples/ryll-web.service` created (TLS-enabled).
- `docs/plans/PLAN-web-frontend.md` Phase 8 → Complete;
  project Complete.
- `docs/plans/index.md` web-frontend project → Complete.
- `README.md` `--web` mode → Shipping with native HTTPS.
- `ARCHITECTURE.md` web-frontend section → "shipped".
- `kerbside/docs/<file>.md` — ryll `--web` cross-ref.

## Estimated total scope

~900–1300 lines across five commits. Heaviest in 8a
(~400 LoC of Rust changes + tests + smoke-test
extension + CI step) and 8c (~300 LoC of docs). 8b is
~150 LoC unit file + docs section. 8d is ~30 LoC in
the kerbside repo. 8e is ~80 LoC of status flips and
prose polish.

## Back brief

Before executing 8a, the implementing agent should
back-brief:

- The exact axum-server version chosen and confirmation
  that its `tls-rustls` feature pulls rustls 0.23 + ring,
  matching the workspace pinning.
- The migration plan from `axum::serve` →
  `axum_server::bind`/`bind_rustls` and how the Phase 6
  explicit-bridge-close sequence remains intact.
- How clap's `requires =` enforces the both-or-neither
  flag pair, including the test that exercises it.
- The `tools/web-smoke.sh` extension shape (new flag vs
  new script), and the CI changes to invoke it.

Before executing 8b, the agent should back-brief: which
directory examples live in (look for existing
`examples/`, `packaging/`, or `etc/` directories to match
the repo's convention), and confirm `Type=simple`
matches ryll's actual blocking behaviour (yes — main.rs's
`runtime.block_on` blocks the main thread).

Before executing 8d, the agent should back-brief which
kerbside file is the most natural home for the cross-ref
before editing.
