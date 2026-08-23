# Audit: HTTP header and file path sanitization

## What we check

### HTTP response header sanitization

Projects using `http.server.BaseHTTPRequestHandler` directly must
override `send_header()` to strip `\r` and `\n` characters from
header values. This prevents HTTP response splitting (CWE-113), which
CodeQL flags as `py/http-response-splitting`.

The canonical implementation is `SafeHeaderMixin` in
`occystrap/util.py`, which calls
`str(value).replace('\r', '').replace('\n', '')` before delegating to
the base class. Every `BaseHTTPRequestHandler` subclass must inherit
from it, listed **first** in the class bases so the MRO reaches the
override.

Projects using Flask (kerbside, shakenfist, agent-python) are
already protected by Werkzeug's `Headers` class, which raises
`ValueError` on a header value containing a line break. Prefer Flask
when adding a new HTTP server; reach for `http.server` only where a
dependency-free embedded server is the point, and then use the
mixin.

### File path sanitization

Projects that construct file paths from user-controlled data --
image names, tags, digests, layer paths -- must validate that the
resulting path stays within the intended base directory. This
prevents path traversal attacks (CWE-22), which CodeQL flags as
`py/path-injection`.

The canonical implementation is `safe_path_join()` in
`occystrap/util.py`, which resolves the joined path with
`os.path.realpath()`, verifies it starts with the base directory, and
raises `PathEscapeError` if it would escape. Use it instead of a bare
`os.path.join()` whenever any component comes from outside the
process. Where a web framework offers its own safe-path helper (such
as Flask's `send_from_directory`), use that.

## Template

No template -- these are code-level patterns. Reference
implementations are in `occystrap/util.py`.

## Projects

| Project | HTTP headers | File paths | Issue |
|---------|-------------|------------|-------|
| agent-python | N/A (Flask) | N/A | - |
| client-python | N/A | N/A | - |
| clingwrap | N/A | N/A | - |
| cloudgood | N/A | N/A | - |
| imago | N/A (Rust) | N/A (Rust) | - |
| kerbside | N/A (Flask) | N/A (Flask) | - |
| kerbside-patches | N/A | N/A | - |
| library-utilities | N/A | N/A | - |
| occystrap | compliant | compliant | - |
| ryll | N/A (Rust) | N/A (Rust) | - |
| shakenfist | N/A (Flask) | N/A (Flask) | - |

N/A: Project does not use raw `BaseHTTPRequestHandler` or
construct file paths from user input, or uses a framework that
provides built-in protection.
