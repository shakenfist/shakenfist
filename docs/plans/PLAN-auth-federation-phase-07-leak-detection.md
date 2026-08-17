# Phase 7 — Leak detection

Planning effort: **high**. The master plan sets no effort for this
phase. High is warranted for two reasons. The first is that the
survey found the phase's compliance argument to be false and found a
checksum-valid credential literal already committed to the
documentation, both of which change the work. The second is that
every deliverable here is a *detector*, and phase 6 established at
length that a detector which cannot fire is worse than no detector at
all — it reports that nothing leaked while checking nothing. Getting
that right is a design problem, not a coding one.

## Scope

Phase 6 stopped secrets reaching a sink. This phase assumes one got
out anyway and shortens the time to notice.

In scope:

* A **runtime detector in functional CI**: the smoke suite queries
  Loki for the `sfk_` credential format across every stream and fails
  if one is there. This is the half the master plan says to build if
  only one gets built.
* A **production detector**: a drop-in Loki ruler alert rule in
  `examples/`, plus the operator documentation that says how to
  install it and what to do when it fires.
* A **repository detector**: a gitleaks job with a custom rule for
  the format, and a `.gitleaks.toml` baselined so it lands green.
* A **unit-level tree scan** which fails if any file in the working
  tree contains a checksum-valid credential — the same idea as
  gitleaks but running inside the already-required check, with no new
  infrastructure and no false positives.
* **Drift tests** binding the format to the rules, so
  `credentials.generate()` and the committed regex cannot diverge
  silently.
* Regenerating the checksum-valid example credential currently
  committed to `docs/user_guide/authentication.md`.

Out of scope, explicitly:

* **Changing the credential format.** Phase 3 shipped it. This phase
  detects it and must match what phase 3 mints, not improve on it.
* **Detecting secrets which are not the `sfk_` format.** Namespace
  key *hashes*, JWTs, `AUTH_SECRET_SEED` and `MARIADB_PASSWORD` have
  no distinguishing shape to scan for; phase 6 stopped them at the
  source, which is the only tractable defence. gitleaks' stock rules
  cover generic third-party credential shapes as a side benefit, but
  that is not what this phase is for.
* **Rotating or purging anything already in a log store.** That is
  phase 6's recorded operator action, documented in
  `docs/operator_guide/credential_rotation.md`.
* **Writing the `secret-handling` consistency audit** in
  `shakenfist/development`. The master plan asserts one exists; it
  does not (see below). Writing it is a change to a different
  repository and is recorded in Future work.
* **Making the gitleaks job a required status check.** That is a
  branch-protection change made in the GitHub UI, not in this
  repository. See Decision 6.
* **`docs/plans/order.yml`**, which never carries phase files.

## What the survey found

The master plan's phase 7 section makes six checkable claims. Five
hold. One is false in a way that removes an argument for the phase
rather than changing its content. The survey additionally found a
committed credential, a piece of existing infrastructure the section
did not know about, and a version constraint which will break the
obvious implementation. The false claim is corrected at source in the
master plan's phase 7 section and in `docs/plans/index.md` as part of
the commit that adds this file, so a later step need not redo it.

### The claims that hold

* **Shaken Fist has no gitleaks job.** Confirmed: `gitleaks` appears
  nowhere in `.github/workflows/` or `.pre-commit-config.yaml`. The
  only matches in the tree are prose, and most of those are ryll
  documentation synced into `docs/components/ryll/`.
* **ryll's `supply-chain.yml` has the working pattern**, and both
  caveats the section quotes are real and are recorded as comments in
  that file: `gitleaks-action@v2` refuses to run on organization
  repositories without a paid licence, so the job installs and
  invokes the upstream binary directly; and gitleaks is only packaged
  from Debian 13 onward, so the job targets `debian-13` while its
  siblings target `debian-12`. The `debian-13` runner label exists —
  `private-ci/conductor/provisioner.py:72` and `imagebuilder.py:67`
  both define it.
* **Phase 3 delivered the format.** `shakenfist/util/credentials.py`
  provides `generate()`, `has_prefix()` and `looks_valid()` over
  `sfk_` + 32 base62 random + 6 base62 CRC32, total length 42.
  Verified by executing it rather than by reading it.
* **Events reach Loki as well as syslog.** `LOG_EVENTS_TO_LOKI`
  (`shakenfist/config.py:629`) is on by default, so an event carrying
  a credential leaves the node.
* **A standing Loki query would have caught step 2g's five sites.**
  Not directly checkable, but phase 6 is the strongest available
  evidence for it: two live leaks were found in phase 6 by querying
  Loki for the credential, and neither had been found by four rounds
  of review of the code that caused them.

### The `secret-handling` audit does exist — this survey got it wrong

The section says:

> Note the `secret-handling` consistency audit in
> `shakenfist/development` already requires a scanner in CI, so this
> phase is also how Shaken Fist becomes compliant with an audit it
> currently fails.

**That is correct, and an earlier draft of this section wrongly
asserted the opposite.** `development/audits/secret-handling.md` was
added on 2026-07-27 by `f5ed399`, three weeks before this survey ran,
and its compliance table lists Shaken Fist as non-compliant against
`shakenfist/shakenfist#3546` with the detail "No secret scanner in CI".
The master plan's claim was right in every particular; the survey
looked in the wrong place and reported a false negative, which was
then committed as a correction to a document that had not been wrong.
Corrected here and in the master plan on 2026-08-17.

So this phase does bring Shaken Fist into compliance with a
cross-project audit it currently fails, which is an additional reason
for it rather than the whole one. The audit's reference invocation had
its own defect — it omitted `--log-opts`, so it scanned every ref —
which this phase found and fixed there in `development` commit
`fd4ddc4`.

The one genuine finding in this area survives: `github-security.md`
recommends GitHub's own secret scanning and push protection be enabled
in repository settings, `development/PLAN-consistency.md:564` records
it as already enabled for Shaken Fist, and
`PROJECT-CONSISTENCY-AUDITS.md:421` still lists it as Disabled. That
table is stale, and correcting it belongs to the other repository.

### A checksum-valid credential is committed to the documentation

`docs/user_guide/authentication.md:43` contains a 42 character literal
beginning `sfk_QKLZ`, which is not a placeholder. (It is quoted here
only by its first eight characters. Writing it out in full would put a
second copy of a possible credential in the repository, and the
tree-scan test this phase adds would refuse the commit — which is a
pleasant way to find out the test works.) Run through the real code:

```
docs example len 42
has_prefix True   looks_valid True
```

It has a correct CRC32 checksum, so it is either a credential a real
cluster minted or one generated by running `generate()` while writing
phase 4's documentation. There is no way to tell from the repository
which, and the honest assumption is the first.

It is the only 42-character `sfk_` literal in the working tree, and —
checked with `git log --all -S` across every branch — the only one in
the entire history. So the history is otherwise clean, and this is a
single-file fix rather than a rewrite.

It matters here because every scanner this phase builds will match
it on their first run. Handling it with an allowlist entry would be
the obvious move and is the wrong one; see Decision 3.

### Functional CI already has a Loki query harness

The master plan's section imagines the runtime detector as something
an operator installs against a production Loki. It did not know that
`shakenfist/deploy/shakenfist_ci/smoke_ci_tests/test_loki.py` already
exists: 169 lines, standing up against a real Loki which
`tools/ci-install-loki.sh` installs for the single-node smoke
topology, with `_query_loki()` and `_await_loki_lines()` helpers that
already do deadline-polling over `query_range` with a token filter.

This is the difference between a detector an operator has to remember
to install and a detector that runs on every functional CI run,
before the code ever reaches a cluster. It is close to free — the
harness is written — and it means a leak of this shape becomes a red
build rather than an alert someone eventually reads. Both are worth
having, and both are in scope, but the CI one is now the cheaper of
the two rather than the more expensive.

One detail makes it work without any special setup: the deploy mints
`_service_key` credentials on every node
(`shakenfist/namespace.py:398`), in the `sfk_` format, as part of
ordinary installation. So a smoke-topology cluster has real minted
credentials in existence by the time the tests run, without the test
arranging anything.

### Debian trixie pins gitleaks to 8.16.0

`apt-cache policy gitleaks` on trixie offers `8.16.0-1+b12`. Upstream
is well past that, and the configuration schema moved in between —
most relevantly, per-rule allowlists became a repeatable
`[[rules.allowlists]]` array in later versions, while 8.16 takes a
single `[rules.allowlist]` table. An implementer who writes the
config against current upstream documentation will produce a file
that 8.16 rejects or, worse, silently parses differently. The rule
file has to be written and validated against the packaged version.

### gitleaks' stock rules will probably fire on day one

`shakenfist/tests/test_vdi_tokens.py:152` and
`shakenfist/tests/external_api/test_admin.py:63` both set a
`private_pem` field to the PEM begin and end delimiters with the
three characters `foo` between them. (Quoted by description rather
than by example, because the stock rule matches those delimiters
wherever they appear, this document included.)

**Superseded during implementation.** The baseline was obtained
after all, without sudo: `apt-get download gitleaks` fetches the
`.deb` as an ordinary user, and `dpkg-deb -x` extracts the binary
without installing it. What it found is in "Baseline results" below,
and it is worse than this section anticipated.

These are placeholders, but gitleaks' stock `private-key` rule
matches the delimiters and not the contents, so it will very likely
flag them — and `gitleaks detect` scans history by default, so
anything ever committed counts. I could not confirm this by running
the tool: it is not installed here and installing it needs sudo. The
plan therefore treats the baseline as unknown-but-probably-nonempty
and makes triage an explicit step which happens *before* the job
lands, rather than a surprise after.

## Decisions

1. **Build the Loki detector first, and land it before gitleaks.**
   The master plan says outright that if only one gets built it
   should be this one, and the survey's discovery that the harness
   already exists makes it the cheapest as well as the most
   valuable. Ordering it first also de-risks the phase: if the
   gitleaks baseline turns out to be a swamp, the valuable half has
   already shipped.

2. **Every detector must prove it can fire, in the same run that
   asserts it did not.** This is the phase's governing rule and it
   comes directly from phase 6, where six leak guards were silently
   emptied and not one announced itself. An assertion that a query
   returned no matches is indistinguishable from an assertion that
   the query was malformed, that the log shipper was down, or that
   Loki was empty. So each detector carries a positive control: the
   CI test emits a synthetic token of the credential shape, confirms
   the detector finds *that*, and only then asserts that it is the
   only thing found. A detector without a positive control is not
   accepted in review.

   The synthetic token is deliberately checksum-invalid, so it
   exercises the regex without ever being a real credential. This
   works because the scanning regex matches the shape, while
   `looks_valid()` verifies the checksum — the two are meant to
   differ, and the difference is what makes a safe control possible.

3. **Regenerate the documentation's example credential as
   checksum-invalid; do not allowlist it.** An allowlist entry is a
   permanent, literal-keyed hole in the scanner, and it would be the
   first thing anybody adds the next time the scanner is
   inconvenient. A checksum-invalid example cannot be a real
   credential no matter how it was produced, so the problem stops
   existing rather than being excepted. It also documents the format
   more honestly, since a reader copying it gets something that
   fails cleanly rather than something that might be somebody's key.

   The same rule applies to every future example: this is what the
   tree-scan test in step 7c enforces.

4. **A unit test scans the tree for checksum-valid credentials, in
   addition to gitleaks.** The overlap is deliberate and the two are
   not redundant. gitleaks scans *history*, covers third-party
   secret shapes, and needs a `debian-13` runner and a packaged
   binary. The unit test scans the *working tree*, understands the
   checksum so it has no false positives, runs inside `tox -ecover`
   which `sanity_checks` already runs and `Can enqueue` already
   requires, and keeps working if the gitleaks job is ever red,
   disabled, or unavailable. The credential format is this project's
   own invention, so the check that guards it should not depend on
   third-party packaging.

5. **The production detector ships as a file, not as prose.** SF does
   not own the operator's Loki — `loki_base_url` defaults to empty
   and is operator-supplied — so nothing in the deploy can install an
   alert rule. The next best thing is a drop-in ruler rule file in
   `examples/`, which is where this repository already puts
   `grafana-dashboard.json`, `mariadb-tuning.cnf` and the load
   balancer configurations. A rule an operator can copy is more
   likely to exist in production than a LogQL expression quoted in a
   paragraph.

6. **The gitleaks job lands as its own `supply-chain.yml` and is not
   wired into `Can enqueue`.** This is the decision most likely to be
   argued with, so the reasoning in full:

   Only three checks are required by the ruleset — `Can see status`,
   `Can enqueue`, `Can merge`
   (`.github/exported-config/ruleset-develop-branch.json:53-65`) —
   and `Can enqueue` aggregates jobs *within* `functional-tests.yml`
   via its `needs:` list. So there are two ways to make gitleaks
   blocking: put the job in `functional-tests.yml` and add it to that
   list, or leave it standalone and add the context to branch
   protection in the UI.

   Standalone is right. `functional-tests.yml` is about exercising a
   deployed cluster; a repository scanner has nothing to do with
   that, and ryll already establishes `supply-chain.yml` as the
   org-wide home for exactly this job, with room for the shellcheck
   and bidi scanners to follow. Making it required is a repository
   settings change which is the operator's to make, tracked
   automatically by the `export-repo-config` workflow when made.

   The credential format — the part this plan actually owns — is
   still gated on every PR, because Decision 4's tree scan runs
   inside the required check. So "not required" applies only to the
   history scan and the third-party rules, which is the right
   trade while the baseline is young.

7. **The drift test parses the committed `.gitleaks.toml` rather than
   restating its regex.** A test that hard-codes the pattern proves
   the test agrees with itself. Reading the file with `tomllib`
   (stdlib from 3.11; the project requires `>=3.11`) and matching
   `credentials.generate()` output against the extracted regex is
   what makes the format and the scanner unable to drift. The same
   test asserts the regex does *not* match a 41- or 43-character
   lookalike, so a future widening of the pattern is visible.

8. **The CI Loki detector queries by regex, not by substring.** `|=
   "sfk_"` would match any log line which merely mentions the prefix
   — including the ones phase 3 and 6 added, which say things like
   "keys beginning with `sfk_` are reserved". `|~
   "sfk_[A-Za-z0-9]{38}"` matches the shape and nothing else. The
   same expression is what ships in the operator alert rule, so the
   two cannot describe different things.

9. **A detected credential is reported without being printed.** The
   failure message names the stream labels, the timestamp and the
   matched line's position, and shows at most the `sfk_` prefix plus
   four characters. A test which dumps the leaked credential into CI
   output — which is itself shipped and retained — has moved the leak
   rather than found it. `testtools`' `addDetail` is attached the
   same way, redacted.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | high | opus | none | Add the runtime leak detector to the smoke suite. Read `shakenfist/deploy/shakenfist_ci/smoke_ci_tests/test_loki.py` first — it has `_query_loki(tokens, start_ns, end_ns)` and `_await_loki_lines()`, deadline-polls `query_range` at `http://localhost:3100`, and skips cleanly when Loki is unreachable; reuse that setUp skip and that base class. Add a new test (same file) which: (1) emits a *positive control* — a synthetic, deliberately checksum-invalid token of the shape `sfk_` + 38 base62 characters, logged through an ordinary API path that reaches Loki (creating a network with the token in its name is the simplest reliable emitter; follow `test_logs_reach_loki`); (2) polls until the control appears via a **regex** query `{job="shakenfist"} \|~ "sfk_[A-Za-z0-9]{38}"`, which proves the query works, the shipper is up and Loki has data — fail with a clear message if the control never arrives, because that is the vacuous case; (3) mints a real credential through the API so a genuine `sfk_` secret exists in this run (`POST` a namespace key with no `key` supplied returns a generated one — see `shakenfist/external_api/auth.py:423`); (4) re-queries the whole window and asserts the *only* matches are the control, failing otherwise. Per Decision 9 the failure message must not print a matched credential: report stream labels, timestamp and `secret[:8]` only, and redact `addDetail` payloads the same way. Note the deploy already mints `_service_key` secrets on every node (`shakenfist/namespace.py:398`), so a passing test is a real statement about a real cluster. Do not add this to `cluster_ci_tests` — Loki only lives at localhost in the single-node smoke topology, which is why the existing test is where it is. Commit subject: "ci: fail the build if a credential reaches Loki." |
| 7b | medium | sonnet | none | Ship the production detector. Add `examples/loki-secret-alert.yaml`: a Loki ruler alerting rule using exactly the expression from step 7a (`count_over_time({job="shakenfist"} \|~ "sfk_[A-Za-z0-9]{38}" [5m]) > 0`), with a `for:` of `0m` (a credential in the log is not a condition to wait out), severity critical, and an annotation pointing at `credential_rotation.md`. Comment the file for an operator who has not used the Loki ruler: where the file goes, that the tenant must match `loki_tenant`, and how to confirm it fires. Then extend `docs/operator_guide/logging.md` — it already has a "Secrets in the log stream" section (line 191) ending with the "treat the log store as sensitive" paragraph. Add a subsection after it, "Detecting a leaked credential", covering: what the `sfk_` format is and why it is greppable (link `docs/user_guide/authentication.md`), the ad-hoc LogQL query for a one-off check, the drop-in rule file for a standing one, the `journalctl -u sf-\*` equivalent for a cluster not shipping to Loki, and a link onward to `credential_rotation.md` for what to do when it fires. Do not restate the format's internals; link the user guide. Check the doc-anchor pre-commit hook passes — it validates cross-page anchor links. Commit subject: "docs: give operators a standing credential leak alert." |
| 7c | medium | sonnet | none | Two changes, both about credentials committed to the repository. First: `docs/user_guide/authentication.md:43` contains a 42 character literal beginning `sfk_QKLZ` which passes `credentials.looks_valid()` — replace it with an example of the same shape which is invalid by construction, and add a short parenthetical to the surrounding prose saying so. Replace the whole thing, body included: keeping the original 32 character body and changing only the checksum would leave the secret material of a possible credential published. The provable construction is a fresh random body with the six checksum characters set to `zzzzzz`, which as base62 is 56800235583 and therefore larger than any CRC32 — no input produces it, so validity fails by arithmetic rather than by luck. Read the surrounding paragraphs; the page explains the prefix's purpose and must still read naturally. Second: add `shakenfist/tests/test_no_committed_credentials.py`, which walks the repository working tree from the repo root (skip `.git`, `.tox`, `*.tgz` and any binary file — decode errors mean skip, not fail), scans each file for `sfk_[A-Za-z0-9]{38}`, and fails on any match for which `credentials.looks_valid()` is True. The failure message must name the file and line but show only the first eight characters of the match (Decision 9). Include a self-test proving the scanner can fire: run the same scanning function over a temporary file containing `credentials.generate()` output and assert it is detected — without that, the test is exactly the vacuous shape phase 6 was about. Commit subject: "test: refuse a real credential in the tree." |
| 7d | medium | opus | none | Add `.gitleaks.toml` at the repository root with a custom rule for the Shaken Fist credential format, and the drift test which binds it to the code. **The packaged gitleaks on Debian trixie is 8.16.0** (`apt-cache policy gitleaks`), which predates the plural `[[rules.allowlists]]` schema — write the file against 8.16's schema (`[[rules]]` with `id`/`description`/`regex`/`tags`, and a singular `[rules.allowlist]` table if one is needed at all) and confirm by reading gitleaks 8.16's own documentation or source rather than current upstream docs. Extend the stock rules (`[extend] useDefault = true`) rather than replacing them. The rule's regex must match the shape only — `sfk_[A-Za-z0-9]{38}` — not the checksum, since a scanner cannot compute one. Then add to `shakenfist/tests/test_credentials.py` a drift test which reads `.gitleaks.toml` with `tomllib` (stdlib; the project requires Python >=3.11), locates the rule by its `id`, compiles the regex, and asserts (i) it matches `credentials.generate()` output over several generations, (ii) it does not match a 41- or 43-character lookalike, and (iii) the rule id is what the workflow and the tree-scan test expect. Do **not** add the workflow in this step. Commit subject: "auth: teach gitleaks the key secret format." |
| 7e | high | opus | worktree | Baseline and land the scanner job. **Baseline first, and do not skip this**: install gitleaks (`sudo apt-get install -y gitleaks`, Debian 13 only) and run `gitleaks detect --source . --redact --verbose --no-banner` over the full history of `develop` with the `.gitleaks.toml` from step 7d. Triage every finding by hand. Known likely hits are the PEM placeholder blocks at `shakenfist/tests/test_vdi_tokens.py:152` and `shakenfist/tests/external_api/test_admin.py:63`, which are literally `-----BEGIN PRIVATE KEY-----\nfoo\n-----END PRIVATE KEY-----` and are false positives — allowlist those by path with a comment saying why. **If any finding is a real credential, stop and report it rather than allowlisting it**: rotation is an operator action and is not this step's to take. Then add `.github/workflows/supply-chain.yml`, modelled closely on `ryll/.github/workflows/supply-chain.yml` — same structure, same concurrency groups, the same two comments explaining why the action is not used and why the job runs on `debian-13`. Triggers: `pull_request` and `push` on `develop`, a weekly cron, and `workflow_dispatch`. One job for now (`gitleaks`), `runs-on: [self-hosted, vm, debian-13, s]`, `actions/checkout@v7` with `fetch-depth: 0`. Do not add it to any `needs:` list in `functional-tests.yml` (Decision 6). Verify with `actionlint`, which is already a pre-commit hook. Record the baseline result — findings triaged, allowlist entries added — in the PR description so the reviewer sees what was excepted and why. Commit subject: "ci: scan the repository for committed secrets." **This brief was substantially overtaken by what the baseline found; read "Baseline results" below for what was actually built and why. In particular the standalone workflow, the `debian-13` runner, the apt install and the path-based allowlists were all replaced.** |

## Risks and mitigations

* **The Loki detector passes because it is broken, not because the
  cluster is clean.** The whole failure mode of phase 6, reproduced
  in a new place. *Mitigation:* Decision 2's positive control is a
  step-7a acceptance requirement, not advice. Reviewer check: delete
  the assertion that the control was found, confirm the test then
  passes against a Loki that is switched off, and restore it. If that
  experiment passes, the test is wrong.

* **gitleaks 8.16 rejects or misreads the config.** The schema
  moved after 8.16 and the obvious sources document the newer one.
  *Mitigation:* step 7d's brief names the version and requires
  validation against it; step 7e runs the real binary over the real
  history before the workflow lands, so a malformed config is found
  by the implementer rather than by CI. Reviewer check: the step 7e
  report quotes the `gitleaks --version` it ran.

* **The baseline is a swamp and the job lands red.** Unknown until
  the tool runs; the PEM placeholders make a non-empty result
  likely. *Mitigation:* the baseline is step 7e's first action and
  gates the workflow's introduction. If triage is large, the step
  stops and reports rather than allowlisting in bulk — a scanner
  whose allowlist was written to make it quiet is not a scanner.

* **A real credential turns up in history.** Possible; the survey
  found the history clean of `sfk_` literals but did not scan for
  third-party secret shapes. *Mitigation:* step 7e stops and reports
  instead of deciding. Rotation and, if warranted, history rewriting
  are the operator's calls, and `credential_rotation.md` already
  documents the rotation half.

* **The alert is noisy in production and gets disabled.** The cost of
  a false positive on a critical alert is the alert being switched
  off. *Mitigation:* Decision 8's 38-character regex cannot match
  prose, and Decision 3 removes the one literal in the repository
  that would have matched. The rule file's comments say what a true
  positive means so an operator can tell the difference on the first
  page.

* **The tree scan slows the unit suite.** It walks every file in the
  repository. *Mitigation:* the tree is small, the regex is cheap,
  and the scan short-circuits on files that fail to decode as text.
  If it exceeds roughly a second, restrict it to tracked files via
  `git ls-files` rather than dropping the test.

## Definition of done

Each item is a statement someone can check and find false.

- [ ] `credentials.generate()` output matches the regex in the
      committed `.gitleaks.toml`, and a 41- and a 43-character
      lookalike do not, asserted by a test which reads the file with
      `tomllib` rather than restating the pattern.
- [ ] No file in the working tree contains a string for which
      `credentials.looks_valid()` is True, asserted by a test which
      also proves it detects a freshly generated credential in a
      temporary file.
- [ ] `docs/user_guide/authentication.md` still shows an example key,
      and `looks_valid()` returns False for it.
- [ ] The smoke suite's Loki detector fails if its positive control
      does not arrive, and fails if a real `sfk_` credential is
      present — demonstrated by the reviewer experiment described in
      Risks, with the result recorded in the PR.
- [ ] `tools/gitleaks-scan.sh` exits 0 on the branch tip, using the
      committed config and the pinned gitleaks version, having scanned
      every commit reachable from HEAD. Its positive control passes, so
      the exit code means "scanned and found nothing".
- [ ] Every entry in the gitleaks allowlist carries a comment saying
      what it excepts and why it is not a credential, and every
      `.gitleaksignore` entry carries a comment saying what was done
      about the credential — the latter enforced by a unit test.
- [ ] Re-adding an accepted finding in a new commit still fails the
      scan, demonstrated rather than asserted.
- [ ] `examples/loki-secret-alert.yaml` exists and uses the same
      LogQL expression as the CI detector — a grep for the expression
      finds it in both places and nowhere else.
- [ ] `docs/operator_guide/logging.md` links both the rule file and
      `credential_rotation.md`, and the doc-anchor hook passes.
- [ ] The master plan's phase 7 section no longer claims a
      `secret-handling` audit exists, and the `docs/plans/index.md`
      row describes what this phase actually built.
- [ ] `pre-commit run --all-files` passes, and `actionlint` accepts
      the new workflow.

## Back brief

Before executing any step, back brief the operator on the
understanding of this plan and how the intended work aligns with it.

Two gates need agreement before the work they belong to starts:

1. **Before step 7e.** The gitleaks baseline is the one unknown in
   this plan. Report what the scan found — how many findings, of what
   kinds, and which are proposed for allowlisting — and get agreement
   before the workflow lands. A real credential in history is a stop.
2. **Before step 7c's documentation edit.** The example credential
   being replaced may have been minted by a real cluster. Confirm
   whether the operator wants it treated as a live credential
   requiring rotation, in addition to being replaced in the page.

## Baseline results

Step 7e's brief said to baseline before landing the workflow, and to
stop and report rather than allowlist if any finding turned out to be
a real credential. One did, so this step stopped and reported. Michael's
answer was that the key was revoked long ago, and to ignore that finding
specifically while going red on anything else — which is what landed.

The scan is `tools/gitleaks-scan.sh`, which runs gitleaks 8.16.0 with
`.gitleaks.toml`. The first baseline used the Debian trixie package,
obtained with `apt-get download` and unpacked with `dpkg-deb -x`; the
job now downloads the upstream release for that same version with a
pinned sha256, because the static runner pool grants no passwordless
sudo. Both binaries report 8.16.0 and produce identical findings.

### Scope: reachable from HEAD, not every ref

The first baseline scanned the default of every ref: 6620 commits,
4m57s, **163 findings**. That number was almost entirely `gh-pages`,
which carries the built documentation site. Its `search/search_index.json`
is one enormous JSON blob quoting every code sample in the docs, so
every finding in a documented example appears there a second time, once
per deploy commit.

Worse, and this invalidated the first triage: **gitleaks 8.16
misattributes those findings**. It reported `search/search_index.json`
hits against `develop` merge commits which do not contain the file at
all — `0c7eacf48` and `0144e36ed`, for instance, whose diffs are three
plan documents and a phase-3 scheduler change respectively. The earlier
draft of this section reported "59 findings reachable from `develop`,
46 of them the built site". That was wrong, and wrong in the direction
that matters: it inflated the backlog with findings which are not in
`develop`'s history, and it did so because the reachability test was
run against commit hashes gitleaks had attributed incorrectly.

Scoping the scan to `--log-opts="HEAD"` removes the misattribution, the
duplication and most of the runtime at once: **5545 commits, 3 seconds,
13 findings**, all genuinely in `develop`'s history. On a pull request
`HEAD` reaches the branch under test *and* all of `develop`, so this is
not a narrowing of the historical claim — it is the same claim, made
correctly.

### The 13 findings

| Rule | Where | Count | Verdict |
|------|-------|-------|---------|
| `private-key` | `ansible/files/id_rsa`, `deploy/ansible-ci/tests/files/id_rsa` | 2 | **Real, and revoked.** See below. |
| `shakenfist-key-secret` | `docs/user_guide/authentication.md` | 1 | The example key step 7c replaced. Checksum-valid. See below. |
| `private-key` | `shakenfist/util/vdi_tokens.py`, `docs/plans/PLAN-kerbside-vdi-tokens-phase-01-signing-key.md` | 2 | False positive. PEM delimiters in a docstring and a schema example, with `...` where key material would be. |
| `generic-api-key` | `ansible/files/grafana/grafana.ini` (×2 paths) | 2 | False positive. `;secret_key = SW2Ycw...` is a *commented-out* line of Grafana's own shipped sample configuration. |
| `generic-api-key` | `shakenfist/external_api/auth.py`, `docs/developer_guide/api_reference/authentication.md` | 2 | False positive. The key *name* `ryll-ci-8fJ2mQ` beside a key elided to `sfk_...`. Key names are not secret. |
| `generic-api-key` | three authentication documents | 3 | False positive. An elided JWT, `eyJhbG...IkpXVCJ9.eyJmc...wwQ`. |
| `generic-api-key` | `shakenfist/tests/test_mariadb_namespace_keys.py` | 1 | False positive. base64 of `$2b$12$fakehash`. |

The ten false positives are allowlisted by content in `.gitleaks.toml`,
not by path and not by fingerprint. Path entries blind a whole file
including a real credential added to it later; fingerprint entries name
a commit, so they would need replacing every time the paragraph around
a placeholder is edited. Content entries survive both.

### The real one

`ansible/files/id_rsa`, added 2020-04-14, and
`deploy/ansible-ci/tests/files/id_rsa`, added 2020-10-04, are the same
3072-bit RSA private key — identical SHA256, comment `mikal@marvin`,
fingerprint `SHA256:mz2lj7UcnApwOkzsnaEhMb+l4gbQQWTah06Vvmi9QCs`.

Neither path exists on `develop` today; they were removed by
`09ada8e7f` and `058cd2cea`. That removes them from the working tree
and from nothing else. This repository is public, so the private key
has been world-readable since April 2020 and is in every clone, every
fork and every mirror. Deleting a file does not unpublish it, and
rewriting the history of a public repository does not either — the
old objects survive in forks and in GitHub's own reflog.

The only remediation which works is to ensure the key authorises
nothing, and Michael confirms it was revoked long ago. The two findings
are therefore accepted in `.gitleaksignore` as fingerprints, which is
the narrowest mechanism available: each covers one occurrence in one
commit, so the same key re-added tomorrow fails the scan again. That
property is verified, not assumed — see below.

### The second one, which is still open

`docs/user_guide/authentication.md` published a **checksum-valid** key
secret from 2026-08-08 until step 7c replaced it. Checksum-valid means
it came from something implementing our format: either
`credentials.generate()` at a shell, in which case it never authorised
anything, or a real cluster, in which case its hash is in that
cluster's `namespace_keys` table and the plaintext has been published on
the website for eight days.

There is no way to tell which from here, and no way to remove it from
history. It is accepted in `.gitleaksignore` with a comment saying to
treat it as disclosed. If any namespace anywhere still holds a key with
this value, delete it — `docs/operator_guide/credential_rotation.md`
covers the mechanics.

### Verification

The scan is green, which is worth nothing on its own — so each
behaviour is checked directly, in a throwaway clone, with a real commit
for each case:

| Case | Expected | Result |
|------|----------|--------|
| The tree and history as they stand | green | green, 5545 commits, 3s |
| Positive control: a planted key secret and a planted SSH key | both reported | both reported, and `tools/gitleaks-scan.sh` fails the build if either is not |
| A freshly minted credential committed to `docs/` | red | red |
| The *ignored* RSA key re-added under a new path in a new commit | red | red |
| A new `zzzzzz` documentation example | green | green |
| A new PEM placeholder in a new document | green | green |

The fourth case is the one that matters for the shape of this
solution: the accepted findings are accepted as *events*, not as
*secrets*, so ignoring them does not create a hole.

### Where the job runs

Decision 6 said the scanner would land as a standalone
`supply-chain.yml`, deliberately outside `Can enqueue`, because a
five-minute full-history scan was too expensive to gate on and would
have been red anyway. Scoping to `HEAD` makes it a three-second job, so
that reasoning no longer holds: it landed as a `credential_scan` job in
`functional-tests.yml`, in both `Can enqueue`'s and `Can merge`'s
`needs` lists, so a credential cannot be merged.

It deliberately does *not* depend on `check_paths`. Every other job
skips for documentation-only changes; this one must not, because the
only real key secret in our history was published in the user guide.
