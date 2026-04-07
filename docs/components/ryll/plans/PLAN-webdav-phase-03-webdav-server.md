# Phase 3: Embedded WebDAV server

## Overview

Integrate a WebDAV server into ryll that serves a local
directory and communicates via in-process byte streams
rather than TCP sockets. This phase builds the server
component in isolation — it takes raw HTTP request bytes as
input and produces raw HTTP response bytes as output. The
integration with the mux protocol layer (connecting the
server to real mux client streams) happens in phase 4.

## Crate choice: `dav-server` + `hyper`

After evaluating the options, the recommended approach is:

**`dav-server`** (v0.11) — a complete RFC 4918 WebDAV
handler with a `LocalFs` filesystem backend. It operates on
`http::Request -> http::Response` (the standard Rust HTTP
types), making it completely transport-agnostic. It does
*not* require hyper as a dependency — it works with the
`http` and `http-body` crate types directly.

**`hyper`** (v1.x, `http1` + `server` features) — used
solely as an HTTP/1.1 framing engine to parse raw bytes
into `http::Request` and serialise `http::Response` back
to bytes. hyper's `http1::Builder::serve_connection()` 
accepts any `AsyncRead + AsyncWrite` type, so we connect
it to a `tokio::io::DuplexStream` — no TCP socket needed.

**Why not `httparse` + manual responses?** The guest's
`spice-webdavd` uses the full WebDAV method set (OPTIONS,
PROPFIND, GET, PUT, DELETE, MKCOL, MOVE, COPY, PROPPATCH,
LOCK, UNLOCK) plus HTTP/1.1 framing features (chunked
transfer-encoding, content-length, keep-alive). Implementing
all of this correctly by hand would be substantial. hyper
handles HTTP framing and `dav-server` handles WebDAV
semantics, both battle-tested.

**Dependency impact**: hyper (with `http1` + `client`
features), hyper-util, http, http-body, and http-body-util
are already in ryll's dependency tree via reqwest. Adding
`server` feature to hyper and adding `dav-server` as a
direct dependency adds minimal compile cost.

## Architecture

```
Per mux client:

  mux bytes ──> DuplexStream (client end)
                    │
                    ▼
               DuplexStream (server end)
                    │
                    ▼
               TokioIo adapter
                    │
                    ▼
               hyper http1::Builder::serve_connection()
                    │
                    ▼
               service_fn(|req| dav_handler.handle(req))
                    │
                    ▼
               dav-server LocalFs backend
                    │
                    ▼
               local filesystem
```

Each mux client gets its own `DuplexStream` pair. Demuxed
HTTP bytes are written to one end; the other end is given
to hyper's `serve_connection()` which parses HTTP requests,
calls the `DavHandler`, and writes HTTP responses back
through the same stream. The response bytes are then read
from the client end and muxed back to the guest.

## Files changed

| File | Change |
|------|--------|
| `Cargo.toml` | Add `dav-server`, `hyper` (with `server` + `http1`), `hyper-util` (with `tokio`), `http`, `http-body`, `http-body-util` |
| `src/webdav/server.rs` | **New file.** `WebdavServer` wrapper around dav-server + hyper |
| `src/webdav/mod.rs` | Add `pub mod server;` |

## Detailed steps

### Step 1: Add dependencies to `Cargo.toml`

```toml
# WebDAV server (RFC 4918 handler with LocalFs backend)
dav-server = { version = "0.11", default-features = false,
               features = ["localfs"] }

# HTTP framing for WebDAV byte-stream transport
hyper = { version = "1", features = ["http1", "server"] }
hyper-util = { version = "0.1",
               features = ["tokio"] }
http = "1"
http-body = "1"
http-body-util = "0.1"
```

Note: `memfs` and `proppatch` features of `dav-server` are
not needed. We only need `localfs` for serving real
directories. Disabling default-features and enabling only
`localfs` keeps the dependency footprint minimal.

Verify that `cargo check` still compiles and that the hyper
version is compatible with what reqwest already pulls in
(should be, since both use hyper 1.x).

### Step 2: Create `src/webdav/server.rs`

This module wraps dav-server and hyper into a simple
interface that the channel handler can use.

**`WebdavServer` struct:**

```rust
pub struct WebdavServer {
    handler: DavHandler,
    read_only: bool,
}
```

The `DavHandler` from `dav-server` is configured with a
`LocalFs` backend pointed at the shared directory.

**Constructor:**

```rust
impl WebdavServer {
    pub fn new(
        root: PathBuf,
        read_only: bool,
    ) -> Result<Self>
```

- Create a `LocalFs` filesystem pointed at `root`.
- Build a `DavHandler` with the filesystem and appropriate
  configuration.
- If `read_only`, configure the handler to reject write
  methods (PUT, DELETE, MKCOL, MOVE, COPY, PROPPATCH). The
  `dav-server` `DavHandler` supports a `read_only()` option
  on the builder — verify this exists, and if not, implement
  it as a wrapper that checks the method before delegating.
- Store `read_only` flag for reference.

**Per-client connection handler:**

```rust
    pub async fn serve_client(
        &self,
        stream: DuplexStream,
    ) -> Result<()>
```

This is the core method that phase 4 will call for each
mux client. It:

1. Wraps the `DuplexStream` in `TokioIo` for hyper
   compatibility.
2. Creates a hyper `http1::Builder`.
3. Calls `serve_connection()` with a `service_fn` that
   delegates to `self.handler.handle()`.
4. Awaits completion (the connection closes when the
   client disconnects or the stream is dropped).

The `DavHandler` is cheaply cloneable (it wraps an `Arc`
internally), so sharing it across concurrent client tasks
is straightforward.

**Type plumbing:**

The main complexity is converting between the body types
that dav-server expects and what hyper produces:

- hyper gives us `http::Request<hyper::body::Incoming>`
- dav-server expects `http::Request<dav_server::body::Body>`
- dav-server returns
  `http::Response<dav_server::body::Body>`
- hyper needs `http::Response<impl http_body::Body>`

Check whether `dav_server::body::Body` implements
`http_body::Body` directly (it should, since dav-server
uses the `http-body` crate). If not, a thin adapter is
needed.

Similarly, hyper's `Incoming` body needs to be converted
to whatever dav-server accepts. The `From<hyper::body::
Incoming>` implementation or `http_body_util::BodyExt`
combinators should handle this.

Document the exact conversions needed once the types are
confirmed by building.

### Step 3: Add unit/integration tests

Write tests in `src/webdav/server.rs`:

- **Smoke test**: create a `WebdavServer` with a temp
  directory, create a `DuplexStream` pair, send a raw
  `OPTIONS /` HTTP request to one end, drive
  `serve_client()` on the other end, read the response,
  verify it's a valid HTTP response with WebDAV methods
  in the `Allow` header.

- **PROPFIND test**: send a `PROPFIND /` request, verify
  the response is a 207 Multi-Status with an XML body
  listing the directory contents.

- **GET test**: create a file in the temp directory, send
  `GET /filename`, verify the response body matches the
  file contents.

- **PUT test**: send `PUT /newfile` with body data, verify
  the file is created in the temp directory with the
  correct contents.

- **PUT read-only test**: create a read-only server, send
  `PUT /newfile`, verify it returns 403 Forbidden.

- **DELETE test**: create a file, send `DELETE /filename`,
  verify the file is removed.

These tests exercise the full stack (raw HTTP bytes →
hyper → dav-server → filesystem → response bytes) without
involving the SPICE channel or mux protocol.

### Step 4: Register module

Add `pub mod server;` to `src/webdav/mod.rs`.

## Dependency notes

The `dav-server` crate's `localfs` feature pulls in:
- `libc` (already in tree)
- `reflink-copy` (small, for CoW file copies)
- `lru` (small, for path caching)
- `parking_lot` (already in tree via egui)
- `xml-rs`, `xmltree` (for WebDAV XML parsing)
- `chrono` (for Last-Modified headers)
- `mime_guess` (for Content-Type headers)
- `htmlescape` (for directory listings)
- `uuid` (for lock tokens)

The XML and chrono dependencies are new but lightweight.
Total additional compile cost should be modest.

## Testing

- `make test` passes (all existing + new tests).
- `cargo fmt --check` and `cargo clippy -- -D warnings`
  pass.
- The new tests in `server.rs` demonstrate that:
  - A `WebdavServer` can be created for a local directory.
  - HTTP requests can be processed through a DuplexStream.
  - PROPFIND, GET, PUT, DELETE work correctly.
  - Read-only mode rejects writes.

## Back brief

Before executing, please confirm your understanding of:
1. This phase builds the WebDAV server as an independent
   component — it is not yet connected to the mux layer
   or the SPICE channel.
2. The integration point is `WebdavServer::serve_client()`,
   which takes a `DuplexStream` and serves one HTTP
   connection over it. Phase 4 will create one DuplexStream
   per mux client and wire them together.
3. The main risk is type plumbing between hyper's body
   types and dav-server's body types. This may require
   adapter code. The step 2 description flags this and
   asks you to resolve it during implementation.
4. Read-only enforcement should be checked — if dav-server
   doesn't support it natively, we wrap the handler.
