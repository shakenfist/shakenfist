# Shaken Fist Project Consistency Plan

**Superseded** by [Consistency audits v2](/components/development/plans/PLAN-consistency-audits-v2/),
whose Phase 4 always intended this file to be retired once issue
tracking was live. Nothing here is a work list any more.

The current answer to "what is this repository missing" is measured
daily rather than written down. The criteria live in
[`docs/audits/`](https://github.com/shakenfist/development/blob/main/docs/audits/README.md),
`scripts/audit-check.py` measures them against a fresh clone of every
in-scope repository each morning, and what it finds is filed as issues
on the repository concerned:
[`org:shakenfist label:consistency is:open`](https://github.com/search?q=org%3Ashakenfist+label%3Aconsistency+is%3Aopen&type=issues).

## What this plan was

From 2026-02-18, a hand audit of every Shaken Fist repository against
the thirteen criteria `PROJECT-CONSISTENCY-AUDITS.md` described at the
time (that file has since been dissolved into
[`docs/audits/`](/components/development/audits/README/)), with a checklist per project of
the cleanups it needed. It ran that way for about three weeks, and
most of what the fleet's automation is built from came out of it:

* The `export-repo-config` reusable workflow and the
  `review-pr-with-claude` composite action in `shakenfist/actions`,
  extracted from per-project copies -- a 168-line workflow and a
  337-line review script that had been copied into every repository
  that wanted them.
* The templates in `templates/`, and the bugs that using them
  exposed: `templates/export-repo-config/` asked for
  `contents: read` while the workflow it calls pushes branches and
  opens pull requests, and the shared review action emitted plain
  markdown where the per-project scripts it replaced emitted
  schema-validated JSON.
* shakenfist, kerbside and imago (now instar) brought to full
  compliance with the criteria as they then stood, as the worked
  examples everything else was measured against.

## Why it was retired

The successor plan replaced every mechanism this one used. Criteria
moved from thirteen sections of one prose file to a directory of
independently checkable specifications -- thirty-four of them by the
time this file was retired -- each linking the template that
implements it. Tracking moved from checkboxes here to issues on the
target repository, where the person fixing one sees it in their own
`gh issue list`. Measurement moved from a working copy on a
developer's laptop to a fresh clone on CI, which is the only version
that can honestly claim to describe what is committed.

Keeping the checklists alongside that would have been harmless if
they had merely gone quiet. A hand-maintained list of what each
project needs decays in both directions at once, and by 2026-08-22
this one had:

* **Instructions that had been reversed.** Ten unticked boxes asked
  five library projects to adopt a "library variant" of the
  indirect-dependency pinning workflow, recording pins in a `pinned`
  extra. That variant was withdrawn: the pins still shipped in the
  published metadata and Renovate's pep621 manager tracks
  `optional-dependencies`, so every recorded version became another
  stream of bump pull requests. Libraries constrain loosely on
  purpose and the audit now leaves them alone -- so the checklist had
  become an instruction to do the wrong thing.
* **Compliance claims contradicted by measurement.** shakenfist,
  kerbside and instar are described here as fully compliant. Against
  the criteria of the day they were; against today's the daily audit
  reports 10, 11 and 7 failing audits respectively, almost all of them
  criteria written after this file stopped being maintained.
* **Work recorded as outstanding that had been done.** client-python
  carried seventeen open boxes and ryll eight, most of them since
  closed by the ordinary issue flow.
* **A fleet that had moved on.** imago is now instar; divergulent,
  sfui and client-python-k3s never appeared here at all; and `actions`
  and `development`, listed here as excluded, are both audited now --
  an exemption for the repository where the standard is written is an
  exemption its authors wrote for themselves.

The per-project checklists are not reproduced above. They described
2026-02 accurately and git history keeps them.

## What outlived it

Three items were still real when this plan was retired.

* **Dead per-project review tooling.** The migration to the shared
  action left `tools/review-pr-with-claude.sh` and
  `tools/create-review-issues.py` behind in instar and occystrap, and
  `tools/create-review-issues.py` in clingwrap, referenced by nothing
  and still documented as live tooling in instar's
  `docs/development.md` and occystrap's `AGENTS.md`. Removed. The rest of that family --
  `address-comments-with-claude.sh`, `render-review.py` and
  `review-schema.json` -- is still what `pr-address-comments.yml`
  calls, in every repository that has it, and stays.
* **kerbside-patches and the shared review action.** This plan asked
  whether the Claude Code logic in `daily-rebase-checks.yml` could use
  `review-pr-with-claude`. It cannot: that workflow runs
  `_build/rebase-with-claude.sh` to rebase the patch series onto a new
  upstream, which is not a pull request review. kerbside-patches has
  no automated review at all, which is a finding the daily audit
  already files as
  [kerbside-patches#949](https://github.com/shakenfist/kerbside-patches/issues/949).
* **Nothing checks the audit scope against the organisation.** The
  matrix, the in-scope list and the excluded list are checked against
  each other and against nothing else, so a repository in none of the
  three is silently unmeasured -- five of them are, as of 2026-08-22.
  Carried forward as
  [development#40](https://github.com/shakenfist/development/issues/40).
