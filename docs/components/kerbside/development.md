# Development

Developer-facing notes for working on Kerbside itself. See
[AGENTS.md](https://github.com/shakenfist/kerbside/blob/develop/AGENTS.md)
for the conventions and common-task recipes, and
[testing.md](/components/kerbside/testing/) for running the test suite, the test
harnesses, and the CI lanes.

## Database migrations

Kerbside uses Alembic for database schema migrations. The migration
files are located in the `kerbside/migrations/versions/` directory.
They live inside the package so that they ship in the wheel and
`kerbside db upgrade` can run them from an install with no
repository checkout present.

### Creating a new migration

```bash
cd /path/to/shakenfist/kerbside
alembic revision -m "description_of_your_changes"
```

This will create a new migration file in
`kerbside/migrations/versions/`. Edit the generated file to add your
schema changes in the `upgrade()` and `downgrade()` functions.

Example:

```python
def upgrade() -> None:
    op.add_column('table_name', sa.Column('column_name', sa.Type()))

def downgrade() -> None:
    op.drop_column('table_name', 'column_name')
```

### Applying migrations

To apply all pending migrations:

```bash
alembic upgrade head
```

To rollback one migration:

```bash
alembic downgrade -1
```

**Note:** Alembic automatically uses the database URL from the
kerbside configuration, so ensure your kerbside config is properly set
up before running migrations.

### Applying migrations to a deployment

The bare `alembic` commands above need a repository checkout, because
they read `alembic.ini` from the repository root. A deployment installed
from a wheel has neither, and uses the CLI instead:

```bash
kerbside db upgrade                        # to head
kerbside db upgrade --revision <revision>  # to a specific revision
kerbside db downgrade --revision <revision>
```

These resolve the migration scripts from inside the installed package,
so they work from any working directory. Both take the database URL from
`SQL_URL`, exactly as the `alembic` commands do, so the same environment
or `/etc/kerbside/kerbside.ini` configuration applies -- see
[configuration.md](/components/kerbside/configuration/).

`downgrade` requires `--revision` and has no default. A downgrade with
an implied target is too easy to run against the wrong database.

Note that a percent sign in `SQL_URL` -- which is what an operator gets
from percent-encoding `@`, `/`, `#` or `!` in a database password -- is
escaped for Alembic automatically. No special handling is needed in the
configuration.

## Diagrams in the documentation

Diagrams in `docs/` are [mermaid](https://mermaid.js.org/) fenced
blocks, which GitHub renders natively -- not ASCII art. Prefer a
vertical flow (`flowchart TD`, `stateDiagram-v2`, `erDiagram`) so a
diagram stays readable on a narrow page; `sequenceDiagram` is the
right choice for a message exchange between two peers.

Two kinds of fenced block in `docs/spice/` are deliberately *not*
diagrams and stay as plain text: the `Offset Size Type Field` byte
tables that document a wire structure field by field, and the
byte-layout boxes in
[spice/protocol-overview.md](/components/kerbside/spice/protocol-overview/). Mermaid's
`packet` diagram is the only construct that fits the latter, and
GitHub's mermaid version does not reliably support it yet, so the
ASCII survives until that changes.

The database entity relationship diagram exists twice: in
[schema.md](/components/kerbside/schema/) and, for standalone viewing, in
`docs/schema.html`. Keep the two in sync -- the `add-database-migration`
skill says to update both.

No CI lane validates the diagrams -- a syntax error renders as an
inline error box on GitHub rather than failing a check. To check a
diagram before pushing, run mermaid-cli over the markdown file that
contains it; mmdc renders each fenced mermaid block and exits non-zero
on a syntax error, which is the actual check:

```shell
npx -p @mermaid-js/mermaid-cli mmdc -i docs/index.md -o /tmp/index-rendered.md
```

## Review tracking

Kerbside receives periodic whole-file human review in addition to the
usual review of changes at pull request time; the current state is in
[REVIEWS.md](https://github.com/shakenfist/kerbside/blob/develop/REVIEWS.md).
Which files count is set by `.vscode/review-scope.toml`: Python, Rust,
shell, and Markdown, less the plan archive in `docs/plans/` and the
generated protobuf stubs.

The state (`REVIEWS.md`, `.vscode/*.weaudit*`) is maintained with
`tools/review-tracking.sh`, a wrapper around the shared helper in the
[shakenfist/development](https://github.com/shakenfist/development/blob/main/docs/code-review-tracking.md)
repository. In a clone it is run by hand, not from git hooks: `prune`
after a pull to discard reviews of files that have since changed,
`stamp` before committing new review marks, `regen` to rebuild
`REVIEWS.md`, `next` to pick an unreviewed file, and `status` to
report effective coverage at HEAD. On develop itself the
`prune-reviews` workflow runs `prune` automatically after every push,
committing the result back as shakenfist-bot, and the daily
consistency audit in shakenfist/development files an issue when five
or more in-scope files need review.

### Signing review marks

A review mark is an attestation, so the commit that introduces one
must be signed -- that signature is what binds the reviewer to the
exact content reviewed. Signing is configured per clone and is easy
to forget in a fresh one; check it before stamping, because an
unsigned review commit records a mark that nothing vouches for:

```bash
git config gpg.format x509
git config gpg.x509.program gitsign
git config commit.gpgsign true
git config tag.gpgsign true
```

Verify with `git log --format='%h %G? %s'`; review commits should
report `U` (signed, with gitsign's Fulcio chain not in a local trust
store) rather than `N` (unsigned). `gitsign` needs an interactive
Sigstore login on first use, so run `gitsign-credential-cache &` to
authenticate once per session instead of once per commit.

The bot's `prune` commits are deliberately unsigned: pruning only
ever removes marks, so it cannot manufacture an attestation. Only
the commits that add marks need signatures.

## Vendored web assets

### Bootstrap CSS

Kerbside uses bootstrap CSS for styling. This was constructed by
downloading Bootstrap 5.3 and jQuery 3.7.0 and then installing to
`kerbside/api/static/js`.

### Axios

Kerbside's web administration API uses Axios for HTTP requests.
Version 1.6.5 is cached at `kerbside/api/static/js`.

### sfui

`kerbside/api/static/sfui` is a vendored copy of
[shakenfist/sfui](https://github.com/shakenfist/sfui), the Shaken
Fist design system: design tokens (`tokens.css`), the shared page
stylesheet (`sf.css`), a theme boot script, the brand logo,
Lit-based web components such as `sf-tabs`, and the vendored Lit
and morphdom libraries those pages need. It is copied in verbatim
by sfui's own `tools/vendor.sh`, which also stamps the copy with
its source commit in `.sfui-commit`.

The design system's own `README.md` is vendored along with it, so
the full contract -- the token rules, what `sf.css` provides and
how its cascade layer works, and the component contract -- is
readable at `kerbside/api/static/sfui/README.md` without leaving
the repository.

Never edit anything under `kerbside/api/static/sfui/` in place:
change the canonical sfui checkout instead and re-vendor, or the
next sync will silently discard the local change. To re-vendor from
a clean, up-to-date sfui checkout:

```shell
tools/vendor.sh <path-to-kerbside>/kerbside/api/static/sfui
```

To check whether the vendored copy has drifted from canonical sfui
without copying anything:

```shell
tools/vendor.sh --check <path-to-kerbside>/kerbside/api/static/sfui
```

Both commands are run from the sfui checkout, not from kerbside.

Re-vendor from sfui's `develop` branch once the change you need has
merged there, not from the branch you made it on. The `sfui-vendor`
consistency audit compares `.sfui-commit` against canonical
`develop` and reports a copy that is behind it, so a stamp naming a
feature branch commit -- or an ancestor of a merge commit -- is
flagged even when every vendored file is byte for byte correct.

### Page polling

Every page rendered with `refresh=True` polls instead of reloading.
`base-sfui.html` wraps the page body in `<main id="kb-content">` and
includes `templates/includes/poll.html`, which every 30 seconds
fetches the current URL, parses that element out of the response and
morphs it onto the live one with the vendored morphdom. The old
`base.html` used a `<meta http-equiv="refresh">`; the morph cycle
exists so that scroll position, selection, focus, an open `<details>`
disclosure and a half-confirmed terminate all survive a tick instead
of being reset by a reload.

Two consequences bind anything added to a polled page:

- **Never attach a listener to rendered content.** Morphing keeps
  live nodes alive across a tick, so a per-node listener added after
  one accumulates a duplicate on every tick. Delegate from
  `#kb-content` or above it, which is what every listener on those
  pages does.
- **A failed poll reports staleness rather than reloading.** An
  expired token answers these routes with a JSON 401, so a reload
  would trade a readable page for an error body. The refresh stamp
  becomes `stale since <time>` and the next tick retries.

## Previewing templates

sfui has no CI of its own, and nothing in kerbside's tox lanes lints
templates or CSS -- flake8 and the unit tests cover Python, and the
HTML smoke tests deliberately assert on fixture data, never on
markup. The only safety net for a converted page's chrome is a human
looking at rendered pixels, in both palettes, without having to stand
up a deployed kerbside first.

`tools/preview-templates.py` renders a converted page through
`kerbside.api`'s own Flask test client -- so routing, context and
Jinja rendering are exactly what a real request would produce -- and
writes it next to a symlink of the real static tree, because the
templates reference their assets as root-relative absolute paths
(`/static/sfui/...`). Only pages that have actually been converted
onto `base-sfui.html` are supported; today that is all five: `login`,
which needs neither authentication nor the database; `consoles`,
whose fixtures render two consoles -- one with sessions and active
tokens, one with neither -- so a single screenshot shows both
terminate states (the two-step disclosure and the dim zero badge);
`sessions`, `sources` and `audit`, each rendered with fixtures that
similarly cover more than one branch (a plain and an errored,
no-CA source; several distinguishable audit events).

The script imports `kerbside.api`, so it needs an interpreter with
kerbside's dependencies installed. The tox environment already has
them, which makes `.tox/py3/bin/python` the interpreter to reach for
after any `tox -epy3` run; a virtualenv with `pip install -e .` works
just as well. A bare system `python3` will not.

```shell
tox -epy3  # only if .tox/py3 does not exist yet
.tox/py3/bin/python tools/preview-templates.py login /tmp/preview
(cd /tmp/preview && python3 -m http.server 8099) &

chromium --headless --disable-gpu --no-sandbox \
    --hide-scrollbars --window-size=1280,1000 \
    --virtual-time-budget=4000 \
    --screenshot=/tmp/login-dark.png \
    http://localhost:8099/login.html

chromium --headless --disable-gpu --no-sandbox \
    --hide-scrollbars --window-size=1280,1000 \
    --virtual-time-budget=4000 \
    --blink-settings=preferredColorScheme=2 \
    --screenshot=/tmp/login-light.png \
    http://localhost:8099/login.html
```

Three details are easy to get wrong:

* Pick a port nothing else is using. 8099 is only an example, and a
  stale server left running from an earlier preview will happily
  serve 404s from a directory that no longer exists.
* Headless Chromium reports `prefers-color-scheme: dark` by default,
  so the plain run above exercises the dark palette;
  `--blink-settings=preferredColorScheme=2` is the only value that
  gives the light one.
* Serve the directory over HTTP, not `file://` -- the theme toggle
  is an ES module, and modules do not load from the `file://`
  scheme.

Then actually look at both PNGs.

A previewed polling page carries the same poll script a live page does,
so it will fetch its own static URL every 30 seconds; against the
statically served preview that returns identical content, which the
poll's own unchanged-content short-circuit skips, so it is harmless by
construction and needs no special handling.

This only covers what renders. The interactive paths -- submitting
a form, a wrong password, the theme toggle, logout -- still need a
browser against a running kerbside, or a hand-check of the relevant
`fetch` calls.

One of those `fetch` calls is worth flagging in advance: a terminate
click (`templates/includes/two-step-terminate.html`) fires a JSON
POST carrying an `X-CSRF-TOKEN` header, read from the non-HttpOnly
`csrf_access_token` cookie, because flask-jwt-extended requires the
double-submit header on cookie-authenticated POSTs. The JWT cookie
is also set `SameSite=Lax`. If a live terminate against a running
kerbside returns an unexpected 401, check for that header before
suspecting the JWT itself -- a missing or stale cookie is the usual
cause. `Authorization: Bearer` callers, such as the CI drivers under
`tools/`, never hit this check; flask-jwt-extended only applies CSRF
protection to cookie-borne tokens.

## Building the Rust proxy

The Rust SPICE proxy lives in `rust/kerbside-proxy/` (its own crate;
`.gitignore`d `target/`). Builds are wrapped in Docker via the crate's
Makefile, which mounts the repo root so the crate can reach
`kerbside/rpc/kerbside.proto`:

```bash
make -C rust/kerbside-proxy build   # cargo build in the kerbside-proxy-dev image
make -C rust/kerbside-proxy test    # cargo test
make -C rust/kerbside-proxy lint    # cargo fmt --check + clippy -D warnings
```

`build.rs` generates the tonic gRPC client from the same
`kerbside/rpc/kerbside.proto` the Python side uses (vendored protoc, no
system protobuf needed). The generator is `tonic-prost-build` and the
generated stubs name types from `tonic` and `tonic-prost`, so those
three crates (plus `prost`) **must be bumped together** — a runtime
crate that moves without its code generator emits stubs that will not
compile. The `tonic-prost-rust` group in `renovate.json` keeps Renovate
proposing them as one PR.

The crate depends on the ryll `shakenfist-spice-protocol` crate as a
git dependency pinned to a specific rev in `Cargo.toml`; bump the `rev`
(and commit the updated `Cargo.lock`) when picking up ryll changes. CI
runs fmt/clippy/test/build via `.github/workflows/rust.yml`; end-to-end
verification against qemu is [direct-qemu-harness.md](/components/kerbside/direct-qemu-harness/).

### Packaging and release

How the wheel is built and how it reaches `PATH` in a deployment is
described in
[How the binary gets there: packaging](/components/kerbside/proxy-architecture/#how-the-binary-gets-there-packaging).
Three things about it only matter while developing:

- **A dev checkout carries a dev-inclusive `kerbside-proxy` FLOOR**
  (`kerbside-proxy>=X.Y.Z.dev0`), not an exact pin: naming a `.dev`
  version opts pip into pre-release resolution, so a git install
  resolves the newest proxy wheel on PyPI — a tagged release or a
  rolling dev wheel published by `dev-proxy-wheel.yml` when the
  binary's inputs change. `tools/stamp-proxy-version.sh <version>`
  REPLACES the floor with the exact `kerbside-proxy==<version>` pin at
  release time (anchored on the `# KERBSIDE_PROXY_PIN` marker), and
  also writes a static version into the crate's pyproject — which is
  how `tools/build-proxy-wheel.sh` knows not to dev-stamp a
  release-stamped tree. A unit test pins the floor's dev-inclusive
  property. `find_proxy_bin()` still prefers `KERBSIDE_PROXY_BIN` and
  the cargo build tree for local work, and the daemon verifies the
  binary's gRPC contract hash at launch either way.
- **`rust.yml` verifies wheel stamping on pull requests as a packaging
  guard** (`tools/verify-wheel-stamping.sh`: an unstamped tree must
  produce a dev-versioned wheel, a release-stamped tree an
  exactly-versioned one), so a change that breaks the maturin build or
  the stamping surfaces before the release tag rather than during it.
- `release.yml` runs the cross-compiled matrix build and publishes both
  packages, proxy first, from a single `v*` tag; `dev-proxy-wheel.yml`
  publishes dev wheels of the proxy (only), unattended. See
  `RELEASE-SETUP.md` for the trusted publishers and the dev-release
  trust posture.

### Validating the firewall against a real client

To exercise the L0+L1 firewall without risking a broken session, run
the warn-only capture in
[direct-qemu-harness.md](/components/kerbside/direct-qemu-harness/): it brings the mock
gRPC server up delivering a `WARN_ONLY` `FirewallPolicy`, drives a real
SPICE client (remote-viewer / virt-viewer / ryll headless) through the
proxy, then asserts `kerbside_proxy_firewall_verdicts_total` is
entirely zero (a clean capture) via `verify-rust-proxy.sh
assert-firewall`.

Any non-zero `observed` verdict on legitimate traffic means the
compiled allowlist or a size cap needs widening — never the verdict
weakening. The same harness has a deny-token / deny-all mode for
exercising the `PermissionDenied` denial path end to end.

To validate live session termination without a full API + daemon +
MariaDB stack, use the termination check in the same harness: the mock
gRPC server's `ProxyControl` stream emits a one-shot `TerminateSession`
a configurable number of seconds after the first authorization
(`MOCK_GRPC_TERMINATE_AFTER`), standing in for the API/DB leg so the
harness exercises the proxy-side cancellation path live.

## Dependency pinning

Indirect (transitive) dependencies are pinned in `pyproject.toml`
between the `# START_OF_INDIRECT_DEPS` and `# END_OF_INDIRECT_DEPS`
marker comments. `tools/pin-indirect-dependencies.sh` regenerates that
block wholesale, nightly, via `pin-indirect-dependencies.yml`. **Never
hand-edit between the markers** — the next run deletes whatever it
finds there. New *direct* dependencies go above the start marker,
preferring an exact version.

Four things about the script are not obvious from the block it
produces:

- **Both markers are load-bearing.** The script hard-fails unless each
  appears exactly once, and unless START comes before END. Transposed
  markers are not a syntax error to the `sed` ranges or the `awk` state
  machine — `/START/,/END/` would then match to end of file and the
  rewrite would silently discard the tail of `pyproject.toml`.
- **The reconcile never moves a version by itself.** Existing pins are
  demoted to pip *constraints* and the direct dependencies are
  re-resolved under them, so the job only adds pins nothing had yet and
  reaps pins nothing requires any more. Renovate stays the only thing
  that raises a version. If the resolve fails with the pins applied as
  constraints, a direct dependency now needs something above its
  current pin.
- **Only `[project] dependencies` are resolved.** The `test` extra's
  transitive dependencies are therefore neither pinned nor reaped.
- **A local dry run rewrites `pyproject.toml` in place.** Discard it
  with `git checkout -- pyproject.toml`. The sort collation is pinned
  to `LC_ALL=C` so a workstation run does not produce a diff made
  entirely of reordering noise.

Packages that must never be pinned carry a `# never-pin: <name>`
comment. The canonical case is pydantic-core, which each pydantic
release exact-pins itself, and which broke every CI install when
Renovate moved the two out of lockstep (PR #198).

## Development configuration

Configuration is loaded from environment variables (`KERBSIDE_*`), then
an INI file at `/etc/kerbside/kerbside.ini`, then the field defaults in
`kerbside/config.py`. That path is hardcoded as `INI_PATH` in
`kerbside/config.py` and there is no setting that relocates it.
Everything lives in one `[kerbside]` section, and each key is
upper-cased and `KERBSIDE_`-prefixed before it reaches the settings
model, so a key is only applied when the corresponding environment
variable is not already set.

`etc/kerbside.conf.example` documents every setting and its default,
and a unit test fails if the two fall out of step. The full reference
is [configuration.md](/components/kerbside/configuration/); the three settings that matter
most when developing are:

- `SQL_URL` — database connection string
- `LOG_OUTPUT_PATH` — set to `stdout` for console logging
- `LOG_VERBOSE` — enable debug logging

## Debugging

Active sessions:

```sql
SELECT * FROM proxychannels;
SELECT * FROM consoletokens WHERE expires > NOW();
```

The daemon supervises the Rust proxy as a single child process (one
tokio task per connection, not a worker process per connection), so
`ps aux | grep kerbside` shows the daemon and its one proxy child. The
proxy's `tracing` output is inherited by the daemon's stderr;
per-channel activity is visible on the Prometheus `/metrics` endpoint.

Common traps:

- **Token expiry.** Console tokens have configurable expiry; the
  maintenance loop must be running to reap expired ones.
- **TLS certificate paths.** The proxy requires valid certificates —
  check `PROXY_HOST_CERT_PATH` and `PROXY_HOST_CERT_KEY_PATH`.
- **Database connections.** SQLAlchemy sessions should be closed
  properly; use context managers or an explicit `session.close()`.
