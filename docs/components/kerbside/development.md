# Development

Developer-facing notes for working on Kerbside itself. See
[AGENTS.md](https://github.com/shakenfist/kerbside/blob/develop/AGENTS.md)
for build commands, conventions, and common tasks, and
[testing.md](/components/kerbside/testing/) for the test harnesses and CI lanes.

## Database migrations

Kerbside uses Alembic for database schema migrations. The migration
files are located in the `alembic/versions/` directory.

### Creating a new migration

```bash
cd /path/to/shakenfist/kerbside
alembic revision -m "description_of_your_changes"
```

This will create a new migration file in `alembic/versions/`. Edit the
generated file to add your schema changes in the `upgrade()` and
`downgrade()` functions.

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
