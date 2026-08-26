# Audit: Credential handling and leak detection

## What we check

Two related things: that credentials do not get written into places
with weaker access control than the credential itself, and that a
scanner is watching for the times they do anyway.

The automated part checks only the scanner. The code-level patterns
below are review criteria -- a grep for them is either trivially evaded
or drowns in false positives -- so a passing check means "a scanner is
running", not "this project has no credentials in its logs".

### A secret scanner runs in CI

Every project with CI must run a repository secret scanner on pull
requests and on pushes to the default branch. `gitleaks` is the
reference implementation; `trufflehog` and `detect-secrets` are
accepted equivalents.

```yaml
  gitleaks:
    name: gitleaks
    runs-on: [self-hosted, vm, debian-13, s]
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0

      - name: Install gitleaks
        run: sudo apt-get update && sudo apt-get install -y gitleaks

      - name: Run gitleaks
        run: >-
          gitleaks detect --source . --log-opts="HEAD" --redact
          --verbose --no-banner
```

Four things in there are not obvious and cost time to rediscover:

* `gitleaks-action@v2` refuses organization repositories without a paid
  licence, so the upstream binary is invoked directly.
* `gitleaks` is packaged from Debian 13 (trixie) onward -- bookworm has
  no package -- so the job needs a `debian-13` runner. Where the pool
  has no passwordless sudo, download the release tarball with a pinned
  version and sha256; pinning is worth doing anyway, because the config
  schema changes between releases.
* `fetch-depth: 0`: a secret committed and then reverted is still in
  the history and still needs rotating. A shallow clone reports a clean
  history it never looked at, which is worse than not running.
* `--log-opts="HEAD"`: without it gitleaks scans *every ref*. On Shaken
  Fist that turned a three second job into five minutes and 13 findings
  into 163, because `gh-pages` carries a documentation search index
  quoting every code sample -- and gitleaks 8.16 *misattributes* those
  findings to unrelated merge commits that do not contain the file, so
  they cannot be triaged by commit either. Any project publishing a
  site from a branch has some version of this. Scoping to `HEAD` is not
  a narrower claim: on a pull request `HEAD` reaches the branch under
  test *and* all of the default branch.

Do not gate the job on a docs-only path filter. Every other job can
skip when only documentation changed; this one cannot, because a
credential pasted into a code sample is a credential. Shaken Fist's one
leaked cluster-minted key secret was published in the user guide.

### The scan needs a positive control

A scan that finds nothing is indistinguishable from a scan that
*cannot* find anything -- a broken regex, a shallow clone, an allowlist
that has grown to swallow everything. Plant a credential in a scratch
directory, scan it, and fail the build if the scanner does not report
it. `shakenfist/tools/gitleaks-scan.sh` plants a key secret and an SSH
private key and refuses to run the real scan until both come back.

### Accepting a finding

History cannot be rewritten to unpublish anything from a public
repository -- the objects survive in every fork and in GitHub's reflog
-- so an accepted finding claims the credential has been dealt with
*where it was trusted*, not tidied out of sight. Never suppress a
finding for a credential that still authorises something.

Two mechanisms, not interchangeable:

* **Content that recurs** -- documentation placeholders, test fixtures,
  an upstream default -- goes in the `[allowlist]` `regexes` list in
  `.gitleaks.toml`, keyed on the text. Editing the paragraph around a
  placeholder produces a new finding in a new commit, so anything keyed
  on a commit would need replacing every time. Avoid `paths`: blinding
  a file also blinds a real credential added to it later.
* **A specific historical event** goes in `.gitleaksignore` as a
  `commit:path:rule-id:line` fingerprint, which forgives one occurrence
  and nothing else. Require a comment on each entry saying what the
  credential was and what was done about it; an undocumented entry is
  indistinguishable from a mistake.

Two gitleaks 8.16 details worth knowing before writing a config:
per-rule allowlists are a single `[rules.allowlist]` table rather than
the repeatable `[[rules.allowlists]]` array the upstream documentation
describes, and global allowlist regexes match the whole match rather
than the secret alone, so anchoring one with `^...$` silently stops it
matching.

This is distinct from the GitHub-hosted secret scanning in
[github-security.md](/components/development/audits/github-security/), which detects known
third-party formats and needs Advanced Security for custom patterns.
This one runs locally, costs nothing, and can be taught a project's own
credential format.

### Credentials do not go into logs or events

Anything written to a log line, an audit event, an exception message or
a metrics label is readable by a wider audience than the credential is,
and usually leaves the machine -- Shaken Fist events go to syslog *and*
Loki, so a credential in an event is a credential in log aggregation.

None of these belong in a log or event payload:

* Bearer tokens and session cookies, including ones just minted and
  ones received on the request being served.
* Passwords and API key secrets in any replayable form. A stored hash
  counts: it is offline-attackable.
* Revocation handles such as a token nonce. Publishing one tells a
  reader which captured tokens are still live.
* Raw HTTP request and response bodies on credential-bearing routes.
  This is the one most often missed, because the logging is generic
  request tracing added long before the route existed.

Log the *identifier* instead -- the key name, the token's `jti`, the
account. Where a framework logs bodies generically, redact by route
rather than by field name: field-name redaction has to know which route
it is on anyway (a field called `key` is a metadata key name on most
endpoints and a secret on a few) and starts leaking silently the day
somebody adds a route it has not heard of.

### Secret-carrying types refuse to stringify

Where a language offers a wrapper type that renders as asterisks, use
it. This turns "remember not to log this" into a property of the type:

* Python: `pydantic.SecretStr`. `str()` and `repr()` yield
  `'**********'`; the value comes back only from `.get_secret_value()`.
* Rust: the `secrecy` crate's `Secret<T>`, or a manual `Debug`
  implementation printing a placeholder. Deriving `Debug` on a struct
  with a secret field is the Rust version of this bug.

The unwrap calls then cluster at the few places that genuinely need
plaintext -- a hash comparison, a signature, an outbound header -- and
each is a place a reviewer can stop and ask whether it belongs there.

### Credentials the project mints are recognisable

Where a project generates a credential rather than accepting one a user
chose, the generated form should carry a short identifying prefix and a
checksum, as GitHub (`ghp_`), GitLab (`glpat-`), Stripe (`sk_live_`)
and Slack (`xoxb-`) do. The prefix makes it greppable in logs and
repositories; the checksum lets a scanner reject lookalikes without an
API call, which is what makes scanning at volume tolerable rather than
alert spam.

This costs nothing cryptographically: a bearer token is a random
identifier, not ciphertext, so a fixed prefix is a label beside a random
value rather than a revealed piece of one. It applies only to
credentials the project generates -- a secret the user chose cannot
carry the prefix, and requiring one would be a breaking API change for
no benefit.

## Template

No template -- the scanner job is a workflow snippet (see above, and
ryll's `.github/workflows/ci.yml` for it in context), and the rest are
code-level patterns.

For a scan grown beyond a single `run:` line -- a positive control, an
allowlist, a shallow-clone guard -- put it in a script in the
repository, as `shakenfist/tools/gitleaks-scan.sh` does. It then runs
the same way locally as in CI, which is the only way anyone will check
a change to it before pushing.

## Projects

<!-- consistency-audit:begin -->
*Generated 2026-08-25T06:54:21.186929+00:00 from `scripts/audit-check.py`; do not edit.*

| Project | Status | Issue |
|---------|--------|--------|
| actions | compliant | - |
| agent-python | non-compliant | shakenfist/agent-python#113 |
| client-python | non-compliant | shakenfist/client-python#354 |
| client-python-k3s | compliant | - |
| clingwrap | non-compliant | shakenfist/clingwrap#111 |
| cloudgood | N/A | - |
| development | compliant | - |
| divergulent | compliant | - |
| instar | compliant | - |
| kerbside | compliant | - |
| kerbside-patches | compliant | - |
| library-utilities | non-compliant | shakenfist/library-utilities#41 |
| occystrap | non-compliant | shakenfist/occystrap#101 |
| private-ci | N/A | - |
| ryll | compliant | - |
| sfui | compliant | - |
| shakenfist | compliant | - |

Details for non-compliant projects:

- **agent-python** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **client-python** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **clingwrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **library-utilities** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
- **occystrap** (Status): No secret scanner in CI; expected one of gitleaks, trufflehog, detect-secrets in a workflow
<!-- consistency-audit:end -->
