# Audit: Moving ref source protection

## What we check

A repository the rest of the fleet consumes at a moving ref has no
private default branch. `shakenfist/actions` serves the shared reusable
workflows and composite actions at `@main`, so what a caller runs is
whatever that branch holds at the moment its job starts, and the caller
reviewed none of it. Local clones of the fleet's default branches held
93 such references across 14 repositories when they were counted on
2026-10-02.

The moving ref is kept rather than pinned, deliberately. Pinning every
caller to a sha moves the trust problem instead of solving it: each of
those references would need updating on every change to the shared
workflows, by a bot with write access to 14 repositories, and a fleet
that updates pins automatically is trusting the updater rather than
the branch. Protecting the one branch every caller reads is the
smaller surface, and it is the surface this criterion measures.

A composite action is the stronger case. A reusable workflow runs as
its own job with the secrets its caller named, while a composite action
runs inside the caller's job, with that job's environment, token and
checkout. Either way the review that protects the caller is the one
that happened on the default branch of the repository it called, which
is why that branch has to be a gate rather than a place anyone with
write access can push.

Two rules make it one, and both have to be in the default branch's
effective rules:

* a `pull_request` rule, so nothing reaches the branch except through a
  pull request; and
* a `required_status_checks` rule, so that pull request waits for CI.

Either on its own leaves a way through. Without the first, a push lands
without a pull request ever existing. Without the second, a pull request
merges with nothing having run, so the pull request is a formality
rather than a review.

A rule that is reported is not necessarily a rule that refuses
anything. `GET /repos/shakenfist/<repo>/rules/branches/<branch>` is
what the check reads, because it is the only endpoint that resolves
organisation and repository rulesets together. So the check collects
the `ruleset_id` of each of the two rules it matched, reads `GET
/repos/shakenfist/<repo>/rulesets/<id>` once per distinct id, and
fails unless every one of them is `enforcement: active`.

What that second read rests on, stated as measurement and inference
rather than as one claim. **Measured:** the effective-rules endpoint
already leaves out a `disabled` ruleset. instar's ruleset `19389569`
is `enforcement: disabled`, targets `~DEFAULT_BRANCH` and carries a
`copilot_code_review` rule, and
`repos/shakenfist/instar/rules/branches/develop` returns only ruleset
`20783686`'s five rules. **Not settled:** whether the endpoint also
omits the rules of a ruleset set to `evaluate`, where a rule is
reported on and nothing is refused. The endpoint's documentation has
at times said that rules from `evaluate` and `disabled` rulesets are
not returned; nothing in this organisation is in `evaluate` mode, and
setting one there to find out would mean changing a live ruleset, so
it has not been measured here.

The read is therefore the conservative choice rather than a known
necessity. If the endpoint does omit `evaluate` rules, the read costs
one API call per contributing ruleset and can never fail; if it does
not, the read is the only thing standing between a rule that merely
reports and a `pass`. The same unsettled question is why an
organisation-sourced rule is an error below rather than a `pass`: the
argument that reaching the endpoint at all implies the rule is active
depends on exactly the behaviour that has not been established.

One more shape is unmeasured, and this page says which way it is
resolved. Whether GitHub returns `{"required_status_checks": []}` for a
rule whose contexts have been cleared, or omits the key, was not
established -- doing so means clearing the contexts on a live ruleset.
The check reads an absent key as the empty list, so the verdict below
is reached under either behaviour. The alternative, treating an absent
key as unreadable, would report a check error for exactly the
configuration the verdict exists to catch, and would make that verdict
unreachable outside the tests. A `required_status_checks` value of the
wrong type is still unreadable, because no reading of it is a cleared
list.

It also fails a `required_status_checks` rule whose context list is
empty. That is the shape an operator leaves behind by clearing the
contexts instead of removing the rule: it satisfies "the rule is
there" while requiring nothing, and a pull request merges as soon as
it is opened. The finding names the checks that belong there -- `Check
paths`, `Lint`, `Unit tests` and `gitleaks` -- so that the issue says
what to put back.

It also fails when some of those contexts are missing rather than all
of them. An operator who deletes two of the four leaves a rule that
still exists and a branch where every pull request still offers a
merge, so nothing but this criterion would report it. The comparison
is against the table on the check, which is the same set the finding
tells an operator to restore: holding the expected contexts and
declining to compare against them would be trusting them exactly as
far as it is convenient.

What is *not* compared is those names against the workflow's job
names. That needs `ci.yml` read out of another repository, which this
criterion does not do, so a context renamed in both the workflow and
the ruleset leaves the table stale and this criterion reporting a
finding until the table is updated. That is why the finding asks an
operator to confirm each context still names a job rather than
asserting that it does. The symptom of getting it wrong in the other
direction is loud: a required context that never reports stops every
pull request offering a merge at once.

Everywhere else the criterion reports `not_applicable`, naming the
repository, and asks GitHub nothing. The table of moving-ref sources
is `MOVING_REF_REPOS` on the check, keyed `org/name` -- an `actions`
repository in somebody else's organisation is not this one -- and each
entry carries that repository's own check-run names, so a repository
joining the table brings its own rather than inheriting these four
into a finding filed against it.

`not_applicable` here means "not in the table", which is not the same
as "not a moving-ref source". The table is a decision about what is
measured, and the gap below is the part of the definition it does not
yet cover.

### Known gap

`shakenfist/development` meets the definition at the top of this page
and is not in the table. `templates/review-tracking/ci-prune-reviews.sh`
clones it with `git clone https://github.com/shakenfist/development`
and no ref pin, exports the result as `SHAKENFIST_DEVELOPMENT`, and
`templates/review-tracking/review-tracking.sh` then `exec`s
`scripts/review-tracking.py` out of that clone. That runs in the
`prune-reviews` job of every adopted repository -- eight of them,
listed in `templates/review-tracking/README.md` -- so whatever
`development`'s default branch holds when one of those jobs starts
executes inside it, which is the same exposure this criterion exists
to measure on `actions`.

It is a gap rather than an omission because closing it is a change to
a second repository's settings, not to this check: `development`'s
`main` carries only `deletion` and `non_fast_forward` rules today, so
adding it to the table would report a fail and file an issue that no
code change here could close. Requiring a pull request and status
checks on `development` is the fix, and adding it to the table is what
should follow; that order is
[#393](https://github.com/shakenfist/development/issues/393). Until
then the table under-reports, and says so here rather than reading as
the complete set.

Anything the audit could not ask is a check error rather than a
verdict. That covers the branch-rules read and each ruleset read
failing or answering something unparseable, a ruleset that reports no
`enforcement`, and a matched rule that names no `ruleset_id` -- with no
id there is nothing to read the enforcement from. It also covers the
cases where every read answered but the question could not be put: a
matched rule whose `ruleset_source_type` is anything other than
`Repository`, or which names no source at all. `Repository` is the
only source whose enforcement is at
`repos/<org>/<repo>/rulesets/<id>`; an `Enterprise` ruleset is not
reachable by this audit at all, a rule naming no source would be read
from this repository on an assumption, and an `Organization` ruleset's
enforcement is only on `GET /orgs/shakenfist/rulesets/<id>`, which the
audit's read-only token is refused. The rule may be refusing
pushes or merely reporting on them, and the audit cannot tell which, so
it says so instead of guessing. Nothing in the fleet is configured that
way today; it becomes measurable when the audit has a read identity
that can reach the organisation endpoint
([#352](https://github.com/shakenfist/development/issues/352)).

### Required, but confirmed by a reviewer

**The bypass list holds exactly one actor.** On `actions`' ruleset that
is the review-marks App, `shakenfist-review-marks`, `actor_id`
5538020, `actor_type` `Integration`, `bypass_mode` `always` -- read
back on 2026-11-11 with a credential that can edit the ruleset, with
`current_user_can_bypass: "never"` for the operator's own credential,
so the App's per-run token is the only identity that can push to the
branch. The bypass is what lets the `prune-reviews` workflow land
review marks on a branch that otherwise requires a pull request.

This is confirmed by a reviewer rather than measured, and for a
measured reason: GitHub omits `bypass_actors` for a caller that cannot
edit the ruleset, and the audit's read-only token cannot, which was
read twice -- the second time under the organisation's security
manager role -- before this criterion was written. Widening that token
to make the check fit would hand the audit the ability to edit every
ruleset in the fleet, which is a worse trade than a reviewer reading a
list. How a reviewer judges an actor by its type is the table in
[bypass-actor-identity.md](/components/development/audits/bypass-actor-identity/); when the audit
gains a read identity that can see the key, a missing key becomes an
error there and here, never an empty set.

**Required approvals are zero, deliberately.** "Requires a pull
request" is not "requires an approval": with one maintainer, an
approval count of one cannot be met by the author of the change. What
the rule buys is that every change is visible as a pull request, with
CI reported on it, before it lands. The fleet's other pull-request
rulesets are configured the same way.

**One accepted residual.** The runner-controller App
`shakenfist-k3s-ci-arc` holds `administration: write` on hunkydory,
where the review-marks App's key is also held, so whoever holds the
runner-controller App's key can widen hunkydory's `review-marks`
deployment branch policy, read the review-marks key from a branch
workflow, and mint push tokens for every repository that App is
installed on -- `actions` among them since 2026-11-11. The chain ends
in a bypass of the very ruleset this criterion measures. It is accepted
knowingly rather than treated as a finding here, because narrowing
another App's permission is its own piece of work;
[bypass-actor-identity.md](/components/development/audits/bypass-actor-identity/) records it as the
residual it is.

## Template

No template -- this is a one-time ruleset configuration change.

To read what is currently enforced:

```bash
gh api repos/shakenfist/<repo> --jq '.default_branch'
gh api repos/shakenfist/<repo>/rules/branches/<default branch> --jq \
    '.[] | {type, ruleset_id, ruleset_source_type}'
gh api repos/shakenfist/<repo>/rulesets/<id> --jq \
    '{enforcement, bypass_actors}'
```

To add the two rules, fetch the ruleset, append them preserving every
other rule and the `~DEFAULT_BRANCH` condition, and PUT it back. A PUT
replaces the ruleset rather than merging into it, so anything left out
of the body is removed -- including `bypass_actors`, which GitHub omits
from the GET for a credential that cannot edit the ruleset, so a body
built from such a read silently empties the list. The GET-then-PUT
shape that guards against that is under *Template* in
[bypass-actor-identity.md](/components/development/audits/bypass-actor-identity/); add these two
entries to its `rules` array:

```json
{"type": "pull_request",
 "parameters": {"required_approving_review_count": 0}}
{"type": "required_status_checks",
 "parameters": {"required_status_checks": [
     {"context": "Check paths", "integration_id": 15368},
     {"context": "Lint", "integration_id": 15368},
     {"context": "Unit tests", "integration_id": 15368},
     {"context": "gitleaks", "integration_id": 15368}]}}
```

`integration_id` 15368 is GitHub Actions; a context without it matches
a check run of that name from any app. The contexts are job names from
the repository's own `ci.yml`, so a repository joining
`MOVING_REF_REPOS` brings its own.

Before trusting the rule, open a throwaway pull request and confirm
every required context resolves to success or skipped and that the
merge button is offered. A required context that never reports wedges
every pull request, and the only way out is an admin edit to the
ruleset. A job that skips satisfies its required check, so a path
filter that skips a lane is not a problem; a job that never runs at all
on the pull requests the repository actually sees is.

## Projects

Per-project compliance for this criterion is regenerated
on every run of the consistency audit: see
[the compliance page](/components/development/audits/compliance/#moving-ref-source-protection).
