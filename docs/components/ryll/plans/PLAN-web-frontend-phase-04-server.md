# Phase 4: HTTP server, token auth, signalling, browser shell

## Prompt

Before responding to questions or making changes, explore the
codebase. Read the master plan at
`docs/plans/PLAN-web-frontend.md` (especially Resolutions §8
plain HTTP, §9 URL token, §10 `include_bytes!`) and the Phase
1 / 2 / 3 plans. Key files for this phase:

- `shakenfist-spice-webrtc/src/bridge.rs` — `WebrtcBridge`,
  `WebrtcBridgeConfig`, `accept_offer(offer_sdp) -> Result<answer_sdp>`,
  `spawn_video_pump`, `spawn_synthetic_audio_pump`,
  `send_control` / `control_rx`. The HTTP `POST /offer`
  handler delegates to `bridge.accept_offer`.
- `shakenfist-spice-renderer/src/encoder/` — `H264Encoder`,
  `EncoderTask`, `SyntheticFrameSource`. Phase 4 wires a
  `SyntheticFrameSource` into the encoder; real SPICE frames
  are deferred to Phase 5.
- `ryll/src/main.rs` — current shape, with `run_headless` and
  `run_gui` entrypoints. Phase 4 adds a `run_web` entrypoint
  selected by a new `--web` CLI flag.
- `ryll/Cargo.toml` — already pulls in `hyper = "1"`,
  `hyper-util`, `http`, `http-body-util` (left over from
  WebDAV pre-extraction; currently unused on the ryll side).
  Phase 4 adds `axum` on top of hyper for ergonomic routing.

External: `axum` 0.8 docs, the rustls `CryptoProvider` setup
from Phase 3 step 3f, RFC 6265 (cookies — n/a; we use URL
query strings, not cookies), and standard browser-WebRTC
`RTCPeerConnection` API (`createOffer`, `setLocalDescription`,
`setRemoteDescription`, `ondatachannel`, `ontrack`).

Flag any uncertainty rather than guessing.

## Goal

Ship the first user-runnable web-frontend artifact. After this
phase:

- `ryll --web session.vv` (or equivalent CLI flags) starts,
  binds an ephemeral TCP port, generates a random per-launch
  token, and prints
  `http://<host>:<port>/?token=<token>` to stdout. The `.vv`
  is parsed for symmetry with other modes but the SPICE
  connection is not actively used in Phase 4 (Phase 5 wires
  real frames; Phase 4 ships `SyntheticFrameSource` so the
  user-facing acceptance criterion is "browser shows a test
  pattern").
- Opening the URL in Firefox or Chrome shows the synthetic
  test pattern from Phase 2 + step 2d, plus a 440 Hz tone in
  the audio output. The full WebRTC handshake completes:
  client offers, server answers, ICE establishes, video and
  audio RTP packets flow, the control DC opens.
- Without the token (e.g. `http://<host>:<port>/`), every
  request returns 401.
- The browser shell is embedded in the binary via
  `include_bytes!` — no sibling `static/` directory at
  deploy time.
- A new `ryll/src/web/` module owns the HTTP server, the
  static-file serving, the `POST /offer` SDP handler, and the
  bridge lifecycle.

Out of scope:

- Real SPICE frames driving the encoder (Phase 5 — the
  `SyntheticFrameSource` ships in 4d unchanged).
- Real Opus passthrough from the SPICE playback channel
  (Phase 5).
- Real input event marshalling or cursor overlay (Phase 5).
- Browser-side reconnect logic (Phase 6).
- HTTPS / TLS (master plan Resolution §8 — deferred).
- Login UI / OIDC / mTLS (master plan Resolution §9 —
  deferred; URL token is what we ship).
- Multi-viewer support. MVP is single-viewer; a second
  `POST /offer` replaces the existing connection.

## Scope

In:

- `ryll/Cargo.toml` gains `axum = "0.8"` (axum re-exports
  hyper internals, so the existing `hyper = "1"` /
  `hyper-util` / `http` / `http-body-util` deps stay relevant
  — keep them).
- A new `ryll/src/web/` module: `mod.rs`, `server.rs`
  (HTTP routing + axum app builder), `assets.rs`
  (`include_bytes!` for index.html / app.js / style.css),
  `signalling.rs` (`POST /offer` handler that calls the
  bridge), and an `assets/` subdirectory with the embedded
  files.
- New CLI flag `--web` (bool) on `Args` (in `ryll/src/config.rs`).
  Optional `--web-port <port>` to pin the listen port (default
  is ephemeral). Optional `--web-host <addr>` to pin the bind
  address (default `127.0.0.1`).
- New `ryll/src/main.rs::run_web` entrypoint. Reuses the
  existing CLI plumbing for `.vv` parsing, virtual disks,
  share dir, capture session, pedantic config — but does not
  spawn the renderer's `run_connection` orchestrator in
  Phase 4. (The orchestrator wiring lands in Phase 5; for
  Phase 4 the SPICE connection is unused.)
- The browser shell: `index.html` + `app.js` + `style.css`,
  embedded via `include_bytes!`. The HTML carries a small
  `<script>` tag with the token templated in (server-side
  string substitution) so the JS can use it on every fetch.
  Or: the JS reads `?token=` from `window.location.search`.
  Pick the latter — simpler and keeps the HTML static.
- A single-viewer model. The `/offer` handler stores the
  active `WebrtcBridge` in an `Arc<Mutex<Option<WebrtcBridge>>>`;
  on a new offer, the existing bridge (if any) is `close()`d
  before the new one is constructed.
- `make test-web` (optional) — an end-to-end test that
  launches `ryll --web --headless-test` and uses a
  `webrtc-rs`-driven Rust client to verify `<video>` track
  flow. Keep this to a minimum; Phase 3's loopback test
  already exercises the bridge end-to-end. The real Phase 4
  acceptance is "the operator opens the URL in a browser and
  sees the test pattern".

Out:

- Every item listed in "Out of scope" above.

## Approach

### HTTP framework choice

Use `axum = "0.8"` on top of the existing `hyper = "1"` deps.
axum is the idiomatic high-level routing layer and re-exports
`hyper` internals where needed, so we don't duplicate
runtime. axum 0.8 has stabilised; its `axum::Router`,
`axum::extract`, `axum::response`, and `axum::serve` APIs are
the surface we'll use.

Alternatives considered:

- **Raw hyper**: would require hand-writing path matching
  and method dispatch. ~150 extra lines for what axum gives
  in 30. Skip.
- **`actix-web`**: heavier dep tree (its own actor runtime).
  Skip.

### Module shape

`ryll/src/web/`:

```
ryll/src/web/
├── mod.rs            # public API: pub fn run(...) -> Result<()>
├── server.rs         # axum Router builder, token middleware
├── assets.rs         # include_bytes! for HTML/JS/CSS, MIME map
├── signalling.rs     # POST /offer handler, single-viewer state
└── assets/
    ├── index.html
    ├── app.js
    └── style.css
```

`ryll/src/web/mod.rs::run` is the single entry point that:

1. Generates a 32-byte random token (hex-encoded, 64 chars).
2. Builds the axum `Router`.
3. Binds the TCP listener (`tokio::net::TcpListener::bind(addr)`).
4. Resolves the local addr (so we know the chosen ephemeral
   port) and prints the URL.
5. Calls `axum::serve(listener, app).await`.

Token-bearing state passed to handlers via axum's
`Extension` extractor. Concretely:

```rust
struct WebState {
    token: String,
    bridge_slot: Arc<Mutex<Option<WebrtcBridge>>>,
    encoder_handle: Arc<Mutex<Option<EncoderInfra>>>,
}

struct EncoderInfra {
    frame_tx: mpsc::Sender<EncodedFrame>,  // for new bridges to consume
    encoder_control: mpsc::Sender<EncoderControl>,
    _task: tokio::task::JoinHandle<...>,
}
```

The encoder runs once at server startup with a
`SyntheticFrameSource` and a single `frame_tx` /
`frame_rx` channel pair. **Wait — the encoder produces *one*
stream that gets consumed by *one* video pump.** A new viewer
can't "tee" the existing stream cheaply. Two options:

- **(a) Encoder per viewer.** Stop the old encoder when the
  bridge is replaced, start a fresh one for the new bridge.
  Simple. Wastes the in-progress encode but MVP is single-
  viewer so the cost is tiny.
- **(b) Single long-lived encoder, broadcast channel.** The
  encoder writes to a `tokio::sync::broadcast::Sender`;
  each bridge reads from its own `Receiver`. Lower
  per-reconnect latency (no encoder reset, no SPS/PPS
  re-emit until the bridge requests a keyframe). More code.

Pick **(a)** for Phase 4. Single-viewer MVP doesn't justify
broadcast complexity. Phase 6 (reconnect / lifecycle) can
revisit if the per-reconnect cost matters.

### Routes

| Method | Path | Handler | Behaviour |
|---|---|---|---|
| GET | `/` | `serve_index` | Returns `index.html` (with `Content-Type: text/html`). Token-gated. |
| GET | `/static/app.js` | `serve_static_js` | Returns `app.js`. Token-gated. |
| GET | `/static/style.css` | `serve_static_css` | Returns `style.css`. Token-gated. |
| POST | `/offer` | `post_offer` | JSON body `{"sdp": "...", "type": "offer"}`. Constructs bridge, calls `accept_offer`, returns `{"sdp": "...", "type": "answer"}`. Token-gated. |
| GET | `*` | (axum default) | 404 |

The `static/*` routes can be a single `serve_static` handler
that switches on the file name. Keep simple.

Token-gating is a tower middleware layer applied to all
routes (not just `/offer`). The middleware reads
`?token=...` from the URL query and compares against
`WebState::token` using `subtle::ConstantTimeEq`. Reject with
`401 Unauthorized` if absent or mismatching.

### Browser shell — `index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ryll — SPICE → browser</title>
  <link rel="stylesheet" href="/static/style.css?token=__TOKEN__"
        id="style-link">
</head>
<body>
  <div id="status">Connecting…</div>
  <video id="video" autoplay playsinline muted></video>
  <script src="/static/app.js?token=__TOKEN__"
          id="app-script"></script>
</body>
</html>
```

But: we said we'd avoid templating and let the JS read the
token from `window.location.search`. So the simpler shape:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ryll — SPICE → browser</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div id="status">Connecting…</div>
  <video id="video" autoplay playsinline muted></video>
  <script src="/static/app.js"></script>
</body>
</html>
```

But then the `/static/style.css` and `/static/app.js` fetches
need a token too. The browser doesn't carry the token from
the page URL to subresource fetches automatically.

**Resolution:** include the token in the subresource URLs by
having the server inject it server-side. The simplest shape:
serve the HTML with a small Rust string substitution that
replaces `{{TOKEN}}` placeholders with the actual token. Not
real templating — just one `replace()` call. Keeps the JS
clean and the static-file URLs gateable.

```rust
async fn serve_index(State(state): State<WebState>) -> impl IntoResponse {
    let body = INDEX_HTML.replace("{{TOKEN}}", &state.token);
    (StatusCode::OK, [(header::CONTENT_TYPE, "text/html; charset=utf-8")], body)
}
```

Where `INDEX_HTML` carries the placeholder:

```html
<link rel="stylesheet" href="/static/style.css?token={{TOKEN}}">
<script src="/static/app.js?token={{TOKEN}}"></script>
```

(We could make this tighter by serving everything from one
big bundle, but that's not worth the build-step complexity.)

For autoplay-without-user-gesture: most browsers allow
autoplay if the video is `muted`. Phase 4 ships with
`<video muted>`; Phase 5 will need a "click to enable audio"
gesture for the audio track to actually play.

### Browser shell — `app.js`

```javascript
// Read token from page URL.
const params = new URLSearchParams(window.location.search);
const TOKEN = params.get("token");
if (!TOKEN) {
    document.getElementById("status").textContent = "Missing token";
    throw new Error("missing token");
}

// We must create a data channel before generating the offer
// (Phase 3 finding) so the SDP carries an m=application
// section that the server's bridge can answer with its
// control DC.
const pc = new RTCPeerConnection();
const dc = pc.createDataChannel("control-seed", { ordered: true });
dc.onopen = () => { /* phase 5 will use this */ };
dc.onmessage = (e) => { /* phase 5 will use this */ };

// Receive the server's video and audio tracks.
pc.ontrack = (event) => {
    if (event.track.kind === "video") {
        document.getElementById("video").srcObject = event.streams[0];
        document.getElementById("status").textContent = "Connected";
    }
    // audio track is auto-played by the browser's default
    // audio sink; nothing to wire here.
};

// addTransceiver(recvonly) so the offer SDP advertises that
// we want to receive video and audio from the server.
pc.addTransceiver("video", { direction: "recvonly" });
pc.addTransceiver("audio", { direction: "recvonly" });

// Drive the SDP exchange.
async function connect() {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    // Wait for ICE gathering complete (no trickle for MVP).
    await new Promise(resolve => {
        if (pc.iceGatheringState === "complete") return resolve();
        const check = () => {
            if (pc.iceGatheringState === "complete") {
                pc.removeEventListener("icegatheringstatechange", check);
                resolve();
            }
        };
        pc.addEventListener("icegatheringstatechange", check);
    });

    const finalOffer = pc.localDescription;
    const response = await fetch(`/offer?token=${TOKEN}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: finalOffer.type, sdp: finalOffer.sdp })
    });
    if (!response.ok) {
        document.getElementById("status").textContent =
            `Server: ${response.status} ${response.statusText}`;
        return;
    }
    const answer = await response.json();
    await pc.setRemoteDescription(new RTCSessionDescription(answer));
}

connect().catch(err => {
    document.getElementById("status").textContent = `Error: ${err}`;
});
```

The minimum that gets the test pattern visible. ~50 lines.

### Browser shell — `style.css`

```css
body { margin: 0; background: #000; color: #fff; font-family: system-ui, sans-serif; }
#status { position: absolute; top: 0.5rem; left: 0.5rem; padding: 0.25rem 0.5rem; background: rgba(0,0,0,0.5); border-radius: 4px; font-size: 0.85rem; }
#video { display: block; width: 100vw; height: 100vh; object-fit: contain; }
```

### `--web` CLI

`ryll/src/config.rs` — add to `Args`:

```rust
/// Run as a SPICE → browser transcoder. Listens on an
/// ephemeral HTTP port, prints a URL with a per-launch
/// random token, and serves a browser shell that consumes
/// the SPICE display via WebRTC.
#[arg(long)]
pub web: bool,

/// Bind address for --web mode (default 127.0.0.1).
#[arg(long, default_value = "127.0.0.1")]
pub web_host: String,

/// Listen port for --web mode (default ephemeral).
#[arg(long, default_value_t = 0u16)]
pub web_port: u16,
```

`ryll/src/main.rs` — dispatch:

```rust
if args.web {
    run_web(config, &args, virtual_disks, share_dir, capture, pedantic_config)
} else if args.headless {
    run_headless(...)
} else {
    run_gui(...)
}
```

`ryll/src/main.rs::run_web` builds the encoder pipeline, the
HTTP server state, and calls `web::run(...)`. Phase 4 does
not invoke the renderer's `run_connection` — that's deferred
to Phase 5.

### Single-viewer state machine

```rust
async fn post_offer(
    State(state): State<WebState>,
    Json(offer): Json<OfferReq>,
) -> Result<Json<OfferRes>, (StatusCode, String)> {
    // Replace any existing bridge.
    let mut slot = state.bridge_slot.lock().await;
    if let Some(old) = slot.take() {
        let _ = old.close().await;
    }

    // Build a new bridge.
    let bridge = WebrtcBridge::new(WebrtcBridgeConfig {
        ice_servers: vec![],
        encoder_control: state.encoder_control.clone(),
    }).await
        .map_err(internal_error)?;

    // Hand the bridge the existing encoder's frame stream.
    // For Phase 4: stop+restart the encoder so the new bridge
    // gets a fresh frame_rx; or use a broadcast channel; per
    // the plan, we stop+restart. State management lives in
    // EncoderInfra::restart.
    let frame_rx = state.encoder.lock().await.restart()?;
    let _ = bridge.spawn_video_pump(frame_rx);
    let _ = bridge.spawn_synthetic_audio_pump();

    let answer_sdp = bridge.accept_offer(offer.sdp).await
        .map_err(bad_request)?;

    *slot = Some(bridge);
    Ok(Json(OfferRes { type_: "answer".into(), sdp: answer_sdp }))
}
```

The `EncoderInfra::restart` helper stops the existing encoder
task (sending `EncoderControl::Stop`), drops the existing
mpsc, spawns a new encoder + new mpsc, returns the new
receiver. Single-flight; the lock is held while restarting.

### rustls CryptoProvider

Per Phase 3 step 3f's finding and the post-Phase-3 audit
follow-up: `WebrtcBridge::new` already calls
`rustls::crypto::ring::default_provider().install_default()`
internally (commit `a2dc11cb`), so Phase 4's HTTP-side code
doesn't need to repeat it. But for safety: also call
`install_default` once at the top of `run_web` so the
provider is set even if no `WebrtcBridge` has been
constructed yet. The call is idempotent.

## Prerequisites

- Phase 3 complete. (It is — commit `8e130a68`.)
- Pre-push audit follow-ups landed. (They are —
  `d4d8755f`, `03f150ab`, `a2dc11cb`, `9f8b4469`.)

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a   | medium | sonnet | none | Add `axum = "0.8"` to `ryll/Cargo.toml`. Create `ryll/src/web/{mod.rs,server.rs}` and the `WebState` struct (token + placeholder for bridge slot + placeholder for encoder infra). Implement an axum `Router` with a token-checking middleware (`tower::ServiceBuilder`) that rejects requests missing or with a wrong `?token=...`. Single placeholder route `GET /` returning `200 "ryll web ok"` for now. `mod.rs::run(addr, port) -> Result<(SocketAddr, ())>` binds a `tokio::net::TcpListener`, queries `local_addr` to discover the chosen ephemeral port, and runs the axum app via `axum::serve`. Generate the token via `rand::random::<[u8; 32]>()` hex-encoded. Add a unit test that constructs the router, makes a request without a token (expect 401), and a request with the right token (expect 200). Worktree not needed; this is greenfield code. Single commit. |
| 4b   | medium | sonnet | none | Add `ryll/src/web/assets.rs` and the `ryll/src/web/assets/{index.html,app.js,style.css}` files. Embed via `include_bytes!`. Implement `GET /static/app.js` and `GET /static/style.css` returning the embedded bytes with appropriate `Content-Type`. Implement `GET /` returning `index.html` with `{{TOKEN}}` placeholders replaced by the runtime token (server-side `String::replace`). The browser shell content is documented in the plan's "Approach → Browser shell" subsection. The JS in 4b should be a no-op stub (`document.getElementById("status").textContent = "loading…"`); real WebRTC wiring lands in 4d. Add unit tests: GET / with token returns 200 and the response body contains the token in the script src. GET /static/app.js with token returns 200 with `text/javascript` content type. GET /static/app.js without token returns 401. Single commit. |
| 4c   | high   | opus  | worktree | Add `POST /offer` handler in `ryll/src/web/signalling.rs`. The handler takes a JSON body `{"type": "offer", "sdp": "..."}`, constructs a `WebrtcBridge` (closing any existing one), wires up the encoder pipeline (the bridge's `spawn_video_pump` consumes `EncodedFrame`s; for Phase 4 the encoder is a long-lived `EncoderTask` driven by `SyntheticFrameSource` that we stop+restart on each new offer), spawns the synthetic audio pump, calls `bridge.accept_offer`, and returns `{"type": "answer", "sdp": "..."}`. Define `EncoderInfra::restart(&mut self) -> Result<mpsc::Receiver<EncodedFrame>>` that stops the existing encoder task, spawns a fresh one, returns the new frame receiver. State held in `Arc<tokio::sync::Mutex<EncoderInfra>>`. Add an integration-style test (hyper-only, no real browser): start the axum app on a background task, POST an SDP offer constructed by a `webrtc-rs` client PC, assert the response is 200 with valid SDP. The test mirrors the Phase 3 step 3f loopback test but goes through HTTP instead of bypassing it. Worktree because this is the most complex glue: lock ordering between `bridge_slot` and `encoder` matters (don't deadlock), and the test exercises a real-ish HTTP flow that may surface edge cases. Single commit. |
| 4d   | medium | sonnet | none | Replace the 4b stub `app.js` with the real WebRTC wiring documented in the plan's "Approach → Browser shell — `app.js`" subsection. The JS reads the token from `window.location.search`, creates an `RTCPeerConnection`, opens a `control-seed` data channel BEFORE generating the offer (the Phase 3 finding requires this so the SDP carries `m=application`), sets up `ontrack` to attach the video track to `<video>`, drives `createOffer` → `setLocalDescription` → wait-for-ICE-complete → POST to `/offer` → `setRemoteDescription`. Update the test expectations in 4b's unit tests if the JS file size or content drifts. Hand-test acceptance: from the worktree, build `make build`, run `cargo run --features capture -- --web --headless` (or `make build && ./target/debug/ryll --web --headless` — figure out the right invocation), open the printed URL in Firefox or Chrome, see the test pattern in the browser. Document the manual-test recipe in the commit message. Single commit. |
| 4e   | medium | sonnet | none | Wire the new `--web` mode into `ryll/src/main.rs::run_web` and `ryll/src/config.rs::Args`. Add the `--web`, `--web-host`, `--web-port` CLI flags. Dispatch in `main`: if `args.web`, call `run_web`; else if `args.headless`, call `run_headless`; else call `run_gui`. The `run_web` body builds the encoder + WebState + axum Router (via `web::run(...)`) and blocks on the server. The renderer's `run_connection` is NOT spawned in Phase 4 — that's deferred to Phase 5. Verify `ryll --web session.vv` prints a URL and starts serving (the .vv argument is parsed for symmetry but the SPICE connection is not made yet). Update `docs/multi-mode-parity.md` with the new web-frontend rows reflecting "available" status for the basic plumbing (HTTP server, token, video track, audio track, datachannel, browser shell) and "missing" for the not-yet-wired items (real frames, real audio, inputs, cursor — all "future, Phase 5"). Update `docs/plans/PLAN-web-frontend.md` execution table flipping Phase 4 to "Complete" and the index. Single commit. |

After 4e, `ryll --web session.vv` prints
`http://127.0.0.1:<ephemeral-port>/?token=<64-hex>` and a
browser opening that URL sees the test pattern.

## Step details

### Step 4a expanded brief

axum 0.8 router shape:

```rust
use axum::{Router, response::IntoResponse, http::StatusCode};
use std::sync::Arc;

pub struct WebState {
    pub token: String,
    // placeholders for 4c:
    pub bridge_slot: Arc<tokio::sync::Mutex<Option<WebrtcBridge>>>,
    pub encoder: Arc<tokio::sync::Mutex<Option<EncoderInfra>>>,
}

pub fn build_router(state: Arc<WebState>) -> Router {
    let token_state = state.clone();
    Router::new()
        .route("/", axum::routing::get(|| async { "ryll web ok" }))
        .layer(axum::middleware::from_fn_with_state(
            token_state,
            check_token,
        ))
        .with_state(state)
}

async fn check_token(
    State(state): State<Arc<WebState>>,
    req: axum::extract::Request,
    next: axum::middleware::Next,
) -> Result<axum::response::Response, StatusCode> {
    let token_param = req.uri().query()
        .and_then(|q| url::form_urlencoded::parse(q.as_bytes())
            .find(|(k, _)| k == "token")
            .map(|(_, v)| v.into_owned()));
    match token_param {
        Some(t) if subtle::ConstantTimeEq::ct_eq(t.as_bytes(), state.token.as_bytes()).into() => {
            Ok(next.run(req).await)
        }
        _ => Err(StatusCode::UNAUTHORIZED),
    }
}
```

Verify the exact axum 0.8 middleware signature against docs;
the `State<Arc<WebState>>` extractor in middleware is the
standard pattern. If `subtle::ConstantTimeEq` is awkward to
plumb (it's a u8-slice comparison; the trait object dance can
be awkward), `subtle::Choice::from((a == b) as u8)` works
too — but use `subtle` rather than `==` to avoid timing-leak
risk on a network-exposed token check.

Add `subtle = "2"` and `url = "2"` to `ryll/Cargo.toml`.

### Step 4c expanded brief

The single-viewer state machine has a few subtleties. The
two locks are:

- `bridge_slot: Arc<Mutex<Option<WebrtcBridge>>>`
- `encoder: Arc<Mutex<Option<EncoderInfra>>>`

`post_offer` needs to (a) close the existing bridge, (b)
restart the encoder, (c) construct a new bridge, (d)
`spawn_video_pump`, (e) `accept_offer`. Lock-ordering rule:
**always take `encoder` before `bridge_slot`**. (The encoder
restart needs to drop the old `frame_tx`, which the old
bridge's video pump task still holds; closing the old bridge
first avoids a "frame_tx still alive" condition that would
block the encoder task from exiting on
`EncoderControl::Stop`.) Concretely:

1. Take `bridge_slot` lock; `slot.take()` → old bridge.
2. Drop `bridge_slot` lock (or hold; either works because
   step 3 doesn't touch bridge_slot).
3. Call `old.close().await` if Some — this drops the bridge,
   which drops its tasks' references to `frame_rx`,
   unblocking the encoder.
4. Take `encoder` lock; call `EncoderInfra::restart()` which:
   - sends `EncoderControl::Stop` on the old encoder's
     control channel,
   - awaits the old encoder's `JoinHandle`,
   - constructs a new `H264Encoder`, new
     `SyntheticFrameSource`, new `(frame_tx, frame_rx)`, new
     `(control_tx, control_rx)`,
   - spawns a new `EncoderTask`,
   - returns the `frame_rx` to the caller.
5. Build the new `WebrtcBridge` with `WebrtcBridgeConfig {
   encoder_control: new_control_tx, ... }`.
6. Take `bridge_slot` lock again; `*slot = Some(new_bridge)`.
7. `bridge.spawn_video_pump(frame_rx)`.
8. `bridge.spawn_synthetic_audio_pump()`.
9. `bridge.accept_offer(offer_sdp).await` → answer SDP.
10. Return JSON `{"type": "answer", "sdp": answer_sdp}`.

If the old encoder doesn't stop within ~2 seconds (it
shouldn't — `Stop` is checked once per tick at 30 fps so
~33 ms), log a warning and proceed; the dangling task will
keep producing into a dropped `frame_tx` and exit on the
next send error.

### Step 4d expanded brief

The 4d JS doesn't deviate from the plan's "Approach"
sketch beyond verifying the exact `RTCPeerConnection`
attribute names (`iceGatheringState`,
`addEventListener("icegatheringstatechange", ...)`,
`new RTCSessionDescription(answer)` constructor accepting
`{type, sdp}` JSON). The browser shell file size will grow
from the 4b stub (~10 lines) to the 4d real (~50 lines);
update any 4b test that asserts the JS file size or content.

The manual-test acceptance is: open the printed URL in
Firefox or Chrome, see the synthetic checkerboard with the
moving band. If the test pattern doesn't appear:

- Open browser devtools → Console. Look for SDP errors,
  `RTCPeerConnection` failures, fetch errors.
- Common failures:
  - 401 from `/offer` → token mismatch (verify the URL has
    `?token=...` and the JS reads it correctly).
  - SDP error → the bridge's answer is malformed (Phase 3
    test should have caught this).
  - ICE failure → server is binding to the wrong address.
    Default `127.0.0.1` works for same-host browsers; for
    cross-host browsers use `--web-host 0.0.0.0`.
  - "MissingExtension" rustls panic → the Phase 4 startup
    didn't install the crypto provider. Verify
    `rustls::crypto::ring::default_provider().install_default()`
    is called early in `run_web`.

## Acceptance criteria

- `make lint` passes after each of 4a, 4b, 4c, 4d, 4e.
- `make test` passes after each step.
- After 4e: `ryll --web session.vv` prints a URL of the form
  `http://127.0.0.1:<port>/?token=<64-hex-chars>`. Opening
  that URL in Firefox or Chrome shows the synthetic test
  pattern (a moving band over a checkerboard) within ~2
  seconds.
- Without the `?token=` query parameter, every HTTP request
  returns 401 Unauthorized.
- `pre-commit run --all-files` passes.
- The `ryll/src/web/` module is self-contained; the `web`
  crate (or module) does not need any new public surface
  exposed from the renderer or the webrtc crate beyond what
  Phase 1–3 already exports.
- Each of 4a–4e is a single commit.

## Risks

- **axum 0.8 API churn.** axum 0.7 → 0.8 introduced
  breaking changes (state-extraction patterns, middleware
  signatures). The brief assumes 0.8; verify against current
  docs before committing 4a.
- **Token timing leak.** Use `subtle::ConstantTimeEq`, not
  `==`. The threat is minor (LAN-only, ephemeral port,
  per-launch token) but the safe pattern is cheap.
- **CSP / mixed-content.** Plain HTTP serves the page;
  WebRTC's DTLS-SRTP transport is encrypted by the protocol
  itself (not by HTTPS). Modern browsers do NOT treat an
  HTTP-served WebRTC page as "insecure" in a way that blocks
  the connection — `RTCPeerConnection` works in HTTP
  contexts. Verified via Phase 2 step 2e research.
- **Browser autoplay policies.** `<video muted autoplay>`
  works in current Firefox and Chrome. Phase 5's audio
  unmute will need a user-gesture-driven toggle.
- **Encoder stop+restart race.** The lock-ordering in 4c
  prevents deadlock but is the trickiest piece of the
  phase. If the test in 4c is hard to write, that's a
  signal to reconsider the broadcast-channel approach
  (Approach option (b)) rather than ship a hard-to-test
  state machine.
- **Browser-side data channel must be created before
  offer.** Phase 3 step 3f finding. The 4d JS includes a
  `pc.createDataChannel("control-seed", ...)` call before
  `pc.createOffer()`. Don't skip this.
- **Worktree base reset.** Earlier sub-agent runs hit
  worktrees based on a stale `develop`. Briefs that use
  worktree isolation should explicitly include
  `git fetch origin && git reset --hard thought-bubble` as
  the first step. (4c is the only worktree-isolated step in
  this phase.)

## Documentation updates

After 4e, update:

- `ARCHITECTURE.md` — add a section on the HTTP signalling
  layer and the browser-shell embedding model. Mention the
  single-viewer + replace-on-new-offer behaviour.
- `AGENTS.md` — note the new `axum` dep and the
  `ryll/src/web/` module's role.
- `README.md` — flip the multi-modal table's "Web" row to
  reflect Phase 4 status (still in progress overall, but
  basic plumbing works).
- `docs/multi-mode-parity.md` — fold in 4e's matrix updates.
- `docs/plans/PLAN-web-frontend.md` — Phase 4 row → Complete.
- `docs/plans/index.md` — Phase 4 marker.
- `docs/web-frontend.md` — DOES NOT EXIST YET; the master
  plan defers operator docs to Phase 8. Phase 4 should NOT
  create that file; just the in-tree docs above.

## Estimated total scope

Roughly 1500–2000 lines of new code across five commits, the
bulk in 4c (encoder + bridge orchestration, ~400 lines) and
4d (the working JS, plus tests, plus the manual-test recipe
in the commit message). 4a is scaffolding (~150 lines), 4b
the embed + static serving (~200 lines + ~80 lines of
embedded HTML/JS/CSS), 4e the wiring + docs (~200 lines).

## Back brief

Before executing 4a, the implementing agent should
back-brief: which axum version got picked, which token
constant-time-eq dep got picked (`subtle`), and how the
middleware extracts the query parameter (`url::form_urlencoded`
or similar).

Subsequent steps follow the same pattern: back-brief first,
edit second.
