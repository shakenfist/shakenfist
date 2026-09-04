# Audit: Renovate groups for lockstep dependency families

## Who this applies to

Python projects with a readable `renovate.json` that declare two or
more members of a family listed in `LOCKSTEP_FAMILIES` in
`scripts/audit/checks/packaging.py`. Everything else is not
applicable: a project declaring one member of a family cannot get that
member out of step with itself, and a rule grouping it would be a rule
that changes nothing.

A project with no `renovate.json` at all is not applicable here. Its
absence is a failure of the [renovate](/components/development/audits/renovate/) criterion, and one
missing file should produce one issue.

Both `[project] dependencies` and every `optional-dependencies` group
are read. Renovate's pep621 manager raises pull requests for both, so a
lockstep family sitting in a test extra churns exactly as much as one
in the runtime set.

## What we check

`renovate.json` has a `packageRules` entry with a `groupName` covering
every declared member of each applicable family, and that entry is not
narrowed by `matchUpdateTypes`.

## Why

Renovate treats every distribution as independent, which is right until
upstream stops doing so. The OpenStack oslo libraries cut coordinated
releases: `oslo.concurrency`, `oslo.config`, `oslo.i18n` and
`oslo.utils` go out together, so an ungrouped project wakes up to four
pull requests raised the same evening for what upstream published as
one event -- four reviews, four CI runs, and four opportunities to
land half of a coordinated upgrade.

Grouping does not make a project depend on less. It makes one upstream
release arrive as one reviewable change, which is the difference
between four pull requests and one for exactly the same upgrade.
Whether a family should be depended on at all is a separate question,
and for oslo the answer is on
[unused-declared-dependency.md](/components/development/audits/unused-declared-dependency/).

`matchUpdateTypes` is excluded deliberately. A rule grouping only the
minor and patch stream leaves major releases arriving one pull request
per package, and for a lockstep family that is the whole problem:
coordinated releases bump every member's major version together.

## The families

The table is seeded with one family, oslo, matched as `^oslo-` against
PEP 503 canonical names.

It is deliberately not seeded with the others. `shakenfist` already
groups pydantic, zope and the grpc stack by hand, and `renovate.json`
in `kerbside` and the client libraries groups some of the same things
in some of the same ways. Promoting those to audited requirements is a
separate decision, because adding a family here files an issue against
every repository that declares two of its members -- including
repositories whose ad-hoc grouping is deliberately narrower.

## Writing the rule

Renovate matches the name as the manifest spells it, so all of these
are accepted, along with the deprecated `matchPackagePatterns`:

```json
{
  "packageRules": [
    {
      "description": "The oslo libraries cut coordinated releases, so one upstream release should arrive as one pull request rather than four",
      "groupName": "oslo",
      "matchPackageNames": ["/^oslo/"]
    }
  ]
}
```

A `!`-prefixed entry excludes, and is honoured wherever it sits in the
list, because Renovate drops a package matched by any negated entry
regardless of order: a rule matching `/oslo/` while excluding
`oslo.config` does not cover the family, written either way round.

The deprecated spellings are read too -- `matchPackagePatterns`,
`excludePackageNames`, `excludePackagePatterns`, the `Prefixes` and
`Dep` variants of each, and the ancient `packageNames` and
`packagePatterns` -- because Renovate reads them. It migrates a
configuration into `matchPackageNames` and `matchDepNames` before it
validates or applies it, so `excludePackageNames` is a `!` entry by
the time any package is matched. This criterion migrates them the same
way rather than growing a branch per spelling. A list holding only
exclusions constrains nothing positively and covers every member bar
the ones it names, which is again how Renovate reads it, and the two
matchers are separate conditions a package must satisfy together, so a
rule setting both covers the intersection.

A rule with no package matcher at all covers nothing. Either it is
narrowed only by a matcher this criterion does not read --
`matchManagers`, `matchDatasources`, `matchFileNames` -- and whether
it reaches these packages depends on a manager the audit does not
model; or it carries no selector whatsoever, which Renovate rejects as
a configuration error rather than applying to every package, so it
groups nothing at all. No repository in the fleet groups a lockstep
family either way; if one starts to, the failure says which family is
ungrouped and the rule can be given an explicit `matchPackageNames`.

## Projects

Per-project compliance for this criterion is regenerated
every morning by the consistency audit: see
[the compliance page](/components/development/audits/compliance/#renovate-lockstep-groups).
