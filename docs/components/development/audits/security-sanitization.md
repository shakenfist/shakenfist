# Audit: HTTP header and file path sanitization

## What we check

Two code-level patterns. One is mechanical and measured here; the
other is a judgment call and is delegated to the pre-push review.

### HTTP response header sanitization

Measured. Every class inheriting an `http.server` request handler --
`BaseHTTPRequestHandler`, or its `SimpleHTTPRequestHandler` and
`CGIHTTPRequestHandler` subclasses, all of which carry the same
`send_header()` -- must also inherit occystrap's `SafeHeaderMixin`,
which strips `\r` and `\n` from header values before delegating to
the base class. A header
value carrying a line break splits the response (CWE-113), which
CodeQL reports as `py/http-response-splitting`.

The mixin must be listed **first** in the bases:

```python
class Handler(
        SafeHeaderMixin,
        http.server.BaseHTTPRequestHandler):
```

Position is what is checked, not mere presence. Listed after the
handler base, the MRO reaches its `send_header()` and the override
never runs -- which is indistinguishable at runtime from not having
the mixin at all.

Flask projects (kerbside, shakenfist, agent-python) are already
protected by Werkzeug's `Headers`, which raises `ValueError` on a
header value containing a line break, and have no `http.server`
handler subclass to find. Prefer Flask for a new HTTP
server; reach for `http.server` only where a dependency-free embedded
server is the point, and then use the mixin.

A subclass that genuinely does not need it -- a test fixture serving
literal headers, say -- carries an `audit-ok: header-sanitization`
comment on or immediately above the `class` statement, ideally with a
reason. The marker is read per class rather than per file, because a
module may hold both a real server and a fixture, and it is read from
a view in which string bodies are blanked and comments survive. A
marker is a comment, and searching the whole file let an ordinary
string constant holding the marker text exempt the class beneath it
from a security check.

A `class` statement whose base list cannot be read is reported rather
than skipped: a skipped class is indistinguishable from a repository
with no handler in it, and on a security check that reads as a clean
bill nobody earned.

The same principle decides how a base is recognised. Bases are
compared as whole names after any dotted prefix is dropped, so
`http.server.BaseHTTPRequestHandler` is in scope while
`MyBaseHTTPRequestHandlerWrapper` is not, and names bound by
`import ... as` are resolved -- `from http.server import
BaseHTTPRequestHandler as BHR` followed by `class Handler(BHR)` is a
handler subclass, and reading it as a bare substring reported the
repository as having no raw HTTP server in it at all. An import
statement is read whole rather than a line at a time, because an
import list long enough to be wrapped -- in parentheses or over a
backslash -- is exactly where an alias hides.

Comments and string literals are blanked before any of this, so a
`class` statement inside a docstring code sample is not a class, and
a `#` or a `)` inside a base list no longer ends the parse early. A
PEP 695 type parameter list between the name and the bases is walked
rather than matched, so `class Handler[T](Base)` is in scope and a
bound holding brackets of its own does not truncate the walk.

What is still not followed is a base bound by plain assignment
(`Base = BaseHTTPRequestHandler`), which needs the kind of name
tracking a full parse gives and this does not attempt. If you write
one, put an `audit-ok` marker on the class with a reason, or spell
the base out.

### File path sanitization

**Delegated to the pre-push review, and not measured here.** Whether a
path built from outside data is proved to stay inside its base
directory is a judgment call about each call site: the same
`os.path.join()` is correct on process-chosen components and a
traversal (CWE-22, `py/path-injection`) on an image name, an archive
member or a request parameter. A grep for the join finds every
correct use alongside every wrong one.

The canonical implementation is `safe_path_join()` in
`occystrap/util.py`, which resolves the joined path with
`os.path.realpath()`, verifies it starts with the base directory, and
raises `PathEscapeError` otherwise. Where a web framework offers its
own helper -- Flask's `send_from_directory` -- use that.

The reviewer is given this standard by the `path-traversal-review`
shared block in each repository's `PUSH-AUDIT.md`. **Coverage for it
is reported by the [push-audit](/components/development/audits/push-audit/) audit**, which checks
that the block is present and current; there is no per-repository
table here, because a table of who carries the block would be a second
copy of the one that audit already publishes.

## Template

No template -- these are code-level patterns. The reference
implementations are `SafeHeaderMixin` and `safe_path_join()` in
`occystrap/util.py`, and the reviewer wording is
`templates/shared-blocks/path-traversal-review.md`.

## Projects

Per-project compliance for the header sanitization check -- the only
part of this criterion with an automated check -- is regenerated every
morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#security-sanitization).
